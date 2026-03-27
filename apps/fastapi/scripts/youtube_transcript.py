"""
YouTube Transcript Extraction via Playwright CDP

Extracts video transcripts using browser automation to bypass IP blocking.
Connects to Playwright server via Chrome DevTools Protocol (CDP).

Usage:
    # As module
    from scripts.youtube_transcript import extract_transcript
    transcript = await extract_transcript("dQw4w9WgXcQ")

    # CLI
    python -m scripts.youtube_transcript dQw4w9WgXcQ
    python -m scripts.youtube_transcript dQw4w9WgXcQ --headed
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page


# CDP endpoints (Tailscale addresses)
CDP_HEADLESS = "https://playwright-cdp-headless.YOUR_TAILNET_DOMAIN.ts.net"
CDP_HEADED = "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"


@dataclass
class TranscriptSegment:
    timestamp: str
    text: str


@dataclass
class TranscriptResult:
    video_id: str
    segments: list[TranscriptSegment]
    raw_text: str


async def extract_transcript(
    video_id: str,
    headless: bool = True,
    cdp_url: Optional[str] = None,
    timeout_ms: int = 15000,
) -> TranscriptResult:
    """
    Extract transcript from a YouTube video.

    Args:
        video_id: YouTube video ID (e.g., 'dQw4w9WgXcQ')
        headless: Use headless browser (faster) or headed (visible in noVNC)
        cdp_url: Custom CDP endpoint URL (overrides headless flag)
        timeout_ms: Timeout for waiting for transcript panel

    Returns:
        TranscriptResult with parsed segments and raw text

    Raises:
        TimeoutError: If transcript doesn't load within timeout
        ValueError: If no transcript is available for the video
    """
    if cdp_url is None:
        cdp_url = CDP_HEADLESS if headless else CDP_HEADED

    url = f"https://www.youtube.com/watch?v={video_id}"

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        try:
            context = await browser.new_context()
            page = await context.new_page()

            # Block video/audio for speed
            await page.route("**/videoplayback*", lambda r: r.abort())
            await page.route("**/googlevideo.com/*", lambda r: r.abort())

            await page.goto(url, wait_until="domcontentloaded")

            # Pause video immediately
            await page.evaluate('document.querySelector("video")?.pause()')
            await page.wait_for_timeout(1500)

            # Expand description
            await page.click("tp-yt-paper-button#expand")
            await page.wait_for_timeout(500)

            # Click "Show transcript"
            await page.click('button[aria-label="Show transcript"]')

            # Wait for transcript content (look for timestamp pattern)
            await page.wait_for_function(
                """() => {
                    const panels = document.querySelectorAll("ytd-engagement-panel-section-list-renderer");
                    for (const p of panels) {
                        if (p.innerText.match(/\\d+:\\d{2}/)) return true;
                    }
                    return false;
                }""",
                timeout=timeout_ms,
            )

            # Extract raw transcript text
            raw_text = await page.evaluate(
                """() => {
                    const panels = document.querySelectorAll("ytd-engagement-panel-section-list-renderer");
                    for (const p of panels) {
                        if (p.innerText.match(/\\d+:\\d{2}/)) {
                            return p.innerText;
                        }
                    }
                    return "";
                }"""
            )

            await context.close()

        finally:
            # Don't disconnect - browser is shared
            pass

    if not raw_text:
        raise ValueError(f"No transcript available for video: {video_id}")

    segments = _parse_transcript(raw_text)
    return TranscriptResult(video_id=video_id, segments=segments, raw_text=raw_text)


def _parse_transcript(raw_text: str) -> list[TranscriptSegment]:
    """Parse raw transcript text into segments with timestamps."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    segments = []

    # Skip header lines (e.g., "Transcript", "Search transcript")
    i = 0
    while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
        i += 1

    # Parse timestamp + text pairs
    while i < len(lines):
        line = lines[i]
        # Match timestamp like "0:01" or "12:34"
        if re.match(r"^\d+:\d{2}$", line):
            timestamp = line
            # Next line might be duration description, skip it
            i += 1
            if i < len(lines) and re.match(r"^\d+\s+(second|minute)", lines[i]):
                i += 1
            # Collect text until next timestamp
            text_parts = []
            while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
                text_parts.append(lines[i])
                i += 1
            if text_parts:
                segments.append(TranscriptSegment(timestamp=timestamp, text=" ".join(text_parts)))
        else:
            i += 1

    return segments


async def main():
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Extract YouTube transcript")
    parser.add_argument("video_id", help="YouTube video ID")
    parser.add_argument("--headed", action="store_true", help="Use headed browser (visible in noVNC)")
    parser.add_argument("--cdp-url", help="Custom CDP endpoint URL")
    args = parser.parse_args()

    print(f"Extracting transcript for: {args.video_id}")
    print(f"Mode: {'headed' if args.headed else 'headless'}")

    t0 = time.time()
    result = await extract_transcript(
        video_id=args.video_id,
        headless=not args.headed,
        cdp_url=args.cdp_url,
    )
    elapsed = time.time() - t0

    print(f"\nExtracted {len(result.segments)} segments in {elapsed:.2f}s\n")
    for seg in result.segments[:5]:
        print(f"  [{seg.timestamp}] {seg.text[:60]}...")
    if len(result.segments) > 5:
        print(f"  ... and {len(result.segments) - 5} more segments")


if __name__ == "__main__":
    asyncio.run(main())
