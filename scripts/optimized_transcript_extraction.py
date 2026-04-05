#!/usr/bin/env python3
"""
Optimized YouTube Transcript Extraction via Playwright CDP.

Key optimizations:
1. Full screen viewport (1920x1080)
2. Remove video player and heavy DOM elements immediately
3. Block more resources (manifests, images, websockets)
4. Disable CSS animations for faster rendering
5. Dynamic wait for transcript segments (not fixed timeout)
6. Detailed logging with progress bar
"""
import asyncio
import json
import os
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.async_api import async_playwright, Page

# Try to import tqdm for progress bar
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    print("[warn] tqdm not available, using simple progress")

# CDP endpoint - use headed server for noVNC visibility
CDP_HEADED = os.environ.get(
    "PLAYWRIGHT_CDP_HEADED",
    "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"
)

# Concurrency settings
MAX_CONCURRENT = 5
MAX_RETRIES = 2
STAGGER_DELAY = 0.5  # Delay between launching browsers to ensure blockers are ready

# Full screen viewport
VIEWPORT = {"width": 1920, "height": 1080}

# =============================================================================
# OPTIMIZED RESOURCE BLOCKING
# =============================================================================
BLOCK_PATTERNS = [
    # VIDEO/AUDIO STREAMING (biggest impact - saves 2-5 seconds)
    "**/videoplayback*",
    "**/googlevideo.com/*",
    "**/*.googlevideo.com/*",
    "**/*.m3u8",              # HLS manifests
    "**/*.ts",                # HLS segments
    "**/manifest*",           # DASH manifests

    # ADS (significant overhead)
    "**/doubleclick.net/*",
    "**/googleadservices.com/*",
    "**/googlesyndication.com/*",
    "**/googleads.g.doubleclick.net/*",
    "**/youtube.com/pagead/*",
    "**/adservice.google.com/*",
    "**/ads?*",
    "**/pagead*",

    # ANALYTICS/TRACKING/LOGGING
    "**/google-analytics.com/*",
    "**/googletagmanager.com/*",
    "**/youtube.com/api/stats/*",
    "**/youtube.com/ptracking*",
    "**/s.youtube.com/*",
    "**/youtubei/v1/log*",
    "**/log_interaction*",

    # RECOMMENDATIONS/BROWSE (not needed)
    "**/browse_ajax*",
    "**/guide_ajax*",
    "**/feed/*",

    # IMAGES (not needed for transcripts)
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.png",
    "**/*.gif",
    "**/*.webp",
    "**/yt3.ggpht.com/*",
    "**/i.ytimg.com/*",
]

BLOCK_RESOURCE_TYPES = {"media", "image", "font"}

# JavaScript to cleanup DOM
CLEANUP_DOM_JS = """
() => {
    const stats = { video: 0, secondary: 0, comments: 0, renderers: 0, timers: 0 };

    // Stop and remove video
    const video = document.querySelector('video');
    if (video) {
        video.pause();
        video.src = '';
        const player = document.querySelector('ytd-player, #movie_player');
        if (player) { player.remove(); stats.video = 1; }
    }

    // Remove sidebar
    const secondary = document.querySelector('#secondary');
    if (secondary) { secondary.remove(); stats.secondary = 1; }

    // Remove comments
    const comments = document.querySelector('#comments');
    if (comments) { comments.remove(); stats.comments = 1; }

    // Remove video renderers
    document.querySelectorAll(
        'ytd-compact-video-renderer, ytd-video-renderer, ytd-grid-video-renderer'
    ).forEach(el => { el.remove(); stats.renderers++; });

    // Kill timers
    const highestId = window.setTimeout(() => {}, 0);
    for (let i = 0; i < highestId; i++) {
        window.clearTimeout(i);
        window.clearInterval(i);
        stats.timers++;
    }

    // Disable analytics
    window.ga = () => {};
    window.gtag = () => {};

    return stats;
}
"""


@dataclass
class BlockStats:
    """Track what was blocked during page load."""
    patterns: int = 0
    types: int = 0

    def __str__(self):
        return f"blocked {self.patterns} patterns, {self.types} by type"


@dataclass
class ExtractionResult:
    """Result of a single video extraction."""
    video_id: str
    success: bool
    segments: int = 0
    content_length: int = 0
    method: str = "none"
    time_seconds: float = 0.0
    error: str | None = None
    block_stats: BlockStats = field(default_factory=BlockStats)
    dom_cleanup: dict = field(default_factory=dict)


def get_cdp_url(endpoint: str) -> str:
    """Get WebSocket URL for CDP connection."""
    parsed = urlparse(endpoint)
    json_url = f"{endpoint}/json/version"

    try:
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urlopen(json_url, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        else:
            with urlopen(json_url, timeout=10) as resp:
                data = json.loads(resp.read().decode())

        ws_url = data.get("webSocketDebuggerUrl", "")
        if not ws_url:
            return endpoint

        ws_parsed = urlparse(ws_url)
        if parsed.scheme == "https":
            return f"wss://{parsed.netloc}{ws_parsed.path}"
        return ws_url

    except Exception as e:
        print(f"[cdp] Failed: {e}")
        return endpoint


async def setup_routes_with_stats(page: Page) -> BlockStats:
    """Set up resource blocking and track what's blocked."""
    stats = BlockStats()

    # Block patterns
    for pattern in BLOCK_PATTERNS:
        async def abort_pattern(route, p=pattern):
            stats.patterns += 1
            await route.abort()
        await page.route(pattern, abort_pattern)

    # Block by resource type
    async def block_by_type(route):
        if route.request.resource_type in BLOCK_RESOURCE_TYPES:
            stats.types += 1
            await route.abort()
        else:
            await route.continue_()
    await page.route("**/*", block_by_type)

    return stats


async def wait_for_segments(page: Page, timeout_ms: int = 10000) -> bool:
    """Dynamic wait for transcript segments to appear."""
    try:
        await page.wait_for_function(
            '''() => {
                const segments = document.querySelectorAll(
                    'transcript-segment-view-model, ytd-transcript-segment-renderer, .segment-text'
                );
                if (segments.length > 0) return true;
                const panel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
                );
                return panel && /\\d+:\\d{2}/.test(panel.innerText);
            }''',
            timeout=timeout_ms
        )
        return True
    except:
        return False


async def extract_transcript(page: Page, video_id: str, pbar=None) -> ExtractionResult:
    """Extract transcript with all optimizations."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    start_time = time.time()
    result = ExtractionResult(video_id=video_id, success=False)

    def log(msg):
        if pbar:
            pbar.set_postfix_str(msg[:40])
        else:
            print(f"  [{video_id}] {msg}")

    try:
        # 1. Setup route blocking BEFORE navigation
        log("Setting up blockers...")
        result.block_stats = await setup_routes_with_stats(page)

        # 2. Navigate (INIT_SCRIPT already injected, video blocked from start)
        log("Navigating...")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # 3. Brief wait for YouTube JS (reduced since no video to load)
        await page.wait_for_timeout(1000)

        # 4. Cleanup any remaining DOM elements
        log("Cleaning DOM...")
        result.dom_cleanup = await page.evaluate(CLEANUP_DOM_JS)

        # 6. Check if already visible
        already_visible = await page.evaluate('''() => {
            const segments = document.querySelectorAll('transcript-segment-view-model, ytd-transcript-segment-renderer');
            return segments.length > 0;
        }''')

        if not already_visible:
            # 7. Expand description
            log("Expanding description...")
            await page.evaluate('''() => {
                const btn = document.querySelector('tp-yt-paper-button#expand:not([hidden])');
                if (btn && btn.offsetParent !== null) {
                    btn.scrollIntoView({ block: 'center' });
                    btn.click();
                }
            }''')
            await page.wait_for_timeout(1000)

            # 8. Click Show transcript
            log("Clicking transcript button...")
            click_result = await page.evaluate('''() => {
                const btn = document.querySelector('[aria-label="Show transcript"]');
                if (btn && btn.offsetParent !== null) {
                    btn.scrollIntoView({ block: 'center' });
                    btn.click();
                    return { success: true };
                }
                const section = document.querySelector('ytd-video-description-transcript-section-renderer');
                if (section) {
                    const sectionBtn = section.querySelector('button');
                    if (sectionBtn) { sectionBtn.click(); return { success: true }; }
                }
                return { success: false };
            }''')

            if not click_result.get('success'):
                # Retry
                await page.wait_for_timeout(1500)
                click_result = await page.evaluate('''() => {
                    const btn = document.querySelector('[aria-label="Show transcript"]');
                    if (btn) { btn.click(); return { success: true }; }
                    return { success: false };
                }''')

            if not click_result.get('success'):
                result.error = "Transcript button not found"
                result.time_seconds = time.time() - start_time
                return result

            # 9. DYNAMIC WAIT for segments
            log("Waiting for segments...")
            if not await wait_for_segments(page, timeout_ms=10000):
                result.error = "Segments did not load"
                result.time_seconds = time.time() - start_time
                return result

        # 10. Extract content
        log("Extracting content...")
        content = await page.evaluate('''() => {
            const segments = document.querySelectorAll('transcript-segment-view-model');
            if (segments.length > 0) {
                const parts = [];
                segments.forEach(seg => parts.push(seg.innerText?.trim() || ''));
                return { count: segments.length, text: parts.join('\\n') };
            }
            const oldSegments = document.querySelectorAll('.segment-text');
            if (oldSegments.length > 0) {
                const parts = [];
                oldSegments.forEach(el => {
                    const container = el.closest('ytd-transcript-segment-renderer');
                    const ts = container?.querySelector('.segment-timestamp')?.innerText?.trim() || '';
                    const text = el.innerText?.trim() || '';
                    if (ts && text) parts.push(ts + '\\n' + text);
                });
                return { count: oldSegments.length, text: parts.join('\\n') };
            }
            return { count: 0, text: '' };
        }''')

        result.segments = content.get('count', 0)
        result.content_length = len(content.get('text', ''))
        result.success = result.segments > 0
        result.method = "dom_scrape" if result.success else "none"
        result.time_seconds = time.time() - start_time

        if result.success:
            log(f"OK: {result.segments} segments")
        else:
            result.error = "No segments extracted"
            log("FAIL: No segments")

    except Exception as e:
        result.error = str(e)[:100]
        result.time_seconds = time.time() - start_time
        log(f"ERROR: {result.error[:30]}")

    return result


async def process_video(video_id: str, browser, semaphore, pbar=None, retry=0) -> ExtractionResult:
    """Process a single video with retry logic."""
    async with semaphore:
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()

        try:
            result = await extract_transcript(page, video_id, pbar)

            if not result.success and retry < MAX_RETRIES:
                await context.close()
                if pbar:
                    pbar.set_postfix_str(f"Retry {retry+1}/{MAX_RETRIES}")
                await asyncio.sleep(1)
                return await process_video(video_id, browser, semaphore, pbar, retry + 1)

            return result
        finally:
            await context.close()


async def run_test(video_ids: list[str]) -> dict:
    """Run the extraction test with progress tracking."""
    print("\n" + "="*70)
    print("OPTIMIZED TRANSCRIPT EXTRACTION TEST")
    print("="*70)
    print(f"Videos:      {len(video_ids)}")
    print(f"Concurrency: {MAX_CONCURRENT}")
    print(f"Viewport:    {VIEWPORT['width']}x{VIEWPORT['height']} (full screen)")
    print(f"Max retries: {MAX_RETRIES}")
    print(f"Started:     {datetime.now().strftime('%H:%M:%S')}")
    print("="*70 + "\n")

    # Connect to CDP
    print("[1/3] Connecting to CDP...")
    cdp_url = get_cdp_url(CDP_HEADED)
    print(f"      URL: {cdp_url[:60]}...")

    start_time = time.time()
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        print(f"\n[2/3] Extracting transcripts...")

        if TQDM_AVAILABLE:
            pbar = tqdm(total=len(video_ids), desc="Progress", unit="video",
                       ncols=80, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}')

            async def process_with_progress(vid, index):
                # Stagger browser launches to ensure blockers are set up
                await asyncio.sleep(index * STAGGER_DELAY)
                result = await process_video(vid, browser, semaphore, pbar)
                pbar.update(1)
                return result

            tasks = [process_with_progress(vid, i) for i, vid in enumerate(video_ids)]
            results = await asyncio.gather(*tasks)
            pbar.close()
        else:
            for i, vid in enumerate(video_ids):
                print(f"  [{i+1}/{len(video_ids)}] Processing {vid}...")
                result = await process_video(vid, browser, semaphore)
                results.append(result)
                status = "OK" if result.success else "FAIL"
                print(f"           {status}: {result.segments} segments in {result.time_seconds:.1f}s")

    # Calculate stats
    elapsed = time.time() - start_time
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    total_segments = sum(r.segments for r in successful)
    total_chars = sum(r.content_length for r in successful)
    total_blocked_patterns = sum(r.block_stats.patterns for r in results)
    total_blocked_types = sum(r.block_stats.types for r in results)

    # Print results
    print(f"\n[3/3] Results")
    print("="*70)
    print(f"Total videos:      {len(video_ids)}")
    print(f"Successful:        {len(successful)} ({100*len(successful)/len(video_ids):.1f}%)")
    print(f"Failed:            {len(failed)} ({100*len(failed)/len(video_ids):.1f}%)")
    print(f"Total segments:    {total_segments:,}")
    print(f"Total characters:  {total_chars:,}")
    print("-"*70)
    print(f"Total time:        {elapsed:.1f}s")
    print(f"Avg time/video:    {elapsed/len(video_ids):.1f}s")
    print(f"Throughput:        {len(video_ids)/elapsed*60:.1f} videos/min")
    print("-"*70)
    print(f"Blocked patterns:  {total_blocked_patterns:,}")
    print(f"Blocked by type:   {total_blocked_types:,}")
    print("="*70)

    if failed:
        print(f"\nFailed videos ({len(failed)}):")
        for r in failed[:10]:
            print(f"  - {r.video_id}: {r.error}")
        if len(failed) > 10:
            print(f"  ... and {len(failed)-10} more")

    print(f"\nCompleted: {datetime.now().strftime('%H:%M:%S')}")

    return {
        "total": len(video_ids),
        "successful": len(successful),
        "failed": len(failed),
        "success_rate": len(successful) / len(video_ids),
        "total_time": elapsed,
        "blocked_patterns": total_blocked_patterns,
        "blocked_types": total_blocked_types,
    }


# Video IDs for testing (25 from "capital global" search)
TEST_VIDEO_IDS = [
    "y_ccK6Uj0uo", "3RgyPPo1uzU", "U0l-rO8faFI", "jrba59bUgNA", "BvAKgTAq058",
    "lX4S3_wAi-4", "StrYEFm938g", "8CU0a0VFMRQ", "h3n-37AB3oc", "Qnl__DjyfFs",
    "FeAezGTly04", "mDb1RMXFDBU", "6geyyQVMQ5k", "kYa9p25-BL0", "2ewl8SVLh9c",
    "ogWQRUWq0Og", "aCM8qGvv7uM", "wSIHCDBM_zw", "ykXM6kNdBJA", "O-uXDA5Lkhg",
    "aJMOLygxttI", "9jRM0T9kSc0", "4rBUAHqKuDw", "0y3F9k_q9WA", "6PILNrL3AXk",
]

if __name__ == "__main__":
    asyncio.run(run_test(TEST_VIDEO_IDS))
