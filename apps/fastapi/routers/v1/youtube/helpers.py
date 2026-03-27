"""
YouTube helpers:
- yt-dlp subprocess for metadata extraction (optimized for speed and completeness)
- Playwright CDP for transcript extraction (bypasses IP blocking)
- youtube-transcript-api with proxy fallback (WARP -> Tor -> Direct) as backup
- ElasticSearch indexing
"""
import asyncio
import logging
import os
import re
import orjson
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import quote
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig
from playwright.async_api import async_playwright

# Use uvicorn's logger for proper output in FastAPI
log = logging.getLogger("uvicorn.error")


# =============================================================================
# yt-dlp Subprocess Extractor (Optimized)
# =============================================================================
class YtDlpExtractor:
    """
    Memory-safe yt-dlp metadata extractor using subprocess.
    Optimized for SPEED and COMPLETENESS of metadata extraction.
    """

    # Base args for all extractions
    # PO Token provider runs as sidecar at localhost:4416
    BASE_ARGS = [
        "yt-dlp",
        "--no-download",
        "--no-warnings",
        "--ignore-errors",
        "--no-clean-info-json",  # Get ALL metadata fields
        "--force-ipv4",
        "--socket-timeout", "15",
        "--retries", "3",
        "--age-limit", "0",  # Skip age-restricted videos
        "--extractor-args", "youtube:skip=dash,hls,translated_subs",  # Speed optimization
        "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",  # PO Token provider
    ]

    def __init__(
        self,
        max_concurrent: int = 10,  # Increased for parallelism
        timeout: float = 60.0,     # Increased for full metadata
        buffer_limit: int = 32 * 1024 * 1024,  # 32MB for large responses
    ):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.timeout = timeout
        self.buffer_limit = buffer_limit

    async def _run_yt_dlp(self, args: list[str], timeout: float | None = None) -> tuple[bool, str, str]:
        """Execute yt-dlp as subprocess with timeout and memory limits."""
        effective_timeout = timeout or self.timeout
        start_time = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=self.buffer_limit,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout
            )
            elapsed = time.time() - start_time
            success = proc.returncode == 0
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")
            if success:
                log.info(f"[yt-dlp] OK ({elapsed:.2f}s) size={len(stdout_str)} bytes")
            else:
                log.info(f"[yt-dlp] FAIL ({elapsed:.2f}s) returncode={proc.returncode} stderr={stderr_str[:200]}")
            return success, stdout_str, stderr_str
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            log.info(f"[yt-dlp] TIMEOUT ({elapsed:.2f}s) limit={effective_timeout}s")
            proc.kill()
            await proc.wait()
            return False, "", "Timeout exceeded"
        except Exception as e:
            elapsed = time.time() - start_time
            log.info(f"[yt-dlp] ERROR ({elapsed:.2f}s) {type(e).__name__}: {e}")
            return False, "", str(e)

    async def extract_video(self, video_id: str) -> dict:
        """Extract FULL metadata for a single video."""
        log.info(f"[yt-dlp:video] extracting id={video_id}")
        url = f"https://www.youtube.com/watch?v={video_id}"
        args = [
            *self.BASE_ARGS,
            "--dump-json",
            "--no-playlist",
            url,
        ]
        async with self.semaphore:
            success, stdout, stderr = await self._run_yt_dlp(args)
        if success and stdout:
            try:
                data = orjson.loads(stdout)
                video = self._normalize_video(data)
                log.info(f"[yt-dlp:video] OK id={video_id} title='{video.get('title', '')[:50]}'")
                return video
            except orjson.JSONDecodeError as e:
                log.info(f"[yt-dlp:video] JSON_ERROR id={video_id}")
                return {"id": video_id, "error": f"JSON parse error: {e}"}
        log.info(f"[yt-dlp:video] FAILED id={video_id} error={stderr[:100] if stderr else 'Unknown'}")
        return {"id": video_id, "error": stderr or "Unknown error"}

    async def extract_batch(self, video_ids: list[str]) -> list[dict]:
        """Extract metadata for multiple videos in parallel."""
        log.info(f"[yt-dlp:batch] starting count={len(video_ids)}")
        start_time = time.time()
        tasks = [self.extract_video(vid) for vid in video_ids]
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - start_time
        ok_count = sum(1 for r in results if "error" not in r)
        log.info(f"[yt-dlp:batch] done count={len(video_ids)} ok={ok_count} failed={len(video_ids)-ok_count} time={elapsed:.2f}s")
        return results

    async def search(
        self,
        query: str,
        max_results: int = 10,
        sort_by_date: bool = False,
    ) -> list[dict]:
        """
        Search YouTube with FULL metadata extraction.
        Step 1: Use --flat-playlist to get video IDs quickly
        Step 2: Extract full metadata for each video in parallel
        """
        prefix = "ytsearchdate" if sort_by_date else "ytsearch"
        search_url = f"{prefix}{max_results}:{query}"
        log.info(f"[yt-dlp:search] query='{query}' max_results={max_results} sort_by_date={sort_by_date}")

        # Step 1: Get video IDs with --flat-playlist (fast)
        args = [
            *self.BASE_ARGS,
            "--flat-playlist",
            "--dump-single-json",
            search_url,
        ]
        async with self.semaphore:
            success, stdout, stderr = await self._run_yt_dlp(args, timeout=60)
        if not success:
            log.info(f"[yt-dlp:search] FAILED to get video IDs query='{query}'")
            return [{"error": stderr or "Search failed"}]

        try:
            data = orjson.loads(stdout)
            entries = data.get("entries", [])
            video_ids = [e.get("id") for e in entries if e and e.get("id")]
            log.info(f"[yt-dlp:search] found {len(video_ids)} video IDs for query='{query}'")
        except orjson.JSONDecodeError:
            log.info(f"[yt-dlp:search] JSON_ERROR query='{query}'")
            return [{"error": "JSON parse error"}]

        if not video_ids:
            return []

        # Step 2: Extract full metadata in parallel (errors handled per-video)
        videos = await self.extract_batch(video_ids)
        ok_videos = [v for v in videos if "error" not in v]
        log.info(f"[yt-dlp:search] OK query='{query}' videos={len(ok_videos)}/{len(video_ids)}")
        return ok_videos

    async def extract_playlist(
        self,
        playlist_id: str,
        max_videos: int = 0,
    ) -> dict:
        """Extract playlist with FULL metadata for all videos."""
        # Dynamic timeout: 10s per video, min 120s for playlists, max 1800s (30min)
        timeout = min(max(max_videos * 10, 120), 1800) if max_videos > 0 else 1800
        log.info(f"[yt-dlp:playlist] extracting id={playlist_id} max_videos={max_videos} timeout={timeout}s")
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        args = [
            *self.BASE_ARGS,
            "--dump-single-json",  # Full metadata
            url,
        ]
        if max_videos > 0:
            args.extend(["--playlist-end", str(max_videos)])

        async with self.semaphore:
            success, stdout, stderr = await self._run_yt_dlp(args, timeout=timeout)
        if not success:
            log.info(f"[yt-dlp:playlist] FAILED id={playlist_id}")
            return {"error": stderr, "videos": []}
        try:
            data = orjson.loads(stdout)
            entries = data.get("entries", [])
            videos = [self._normalize_video(e) for e in entries if e]
            log.info(f"[yt-dlp:playlist] OK id={playlist_id} title='{data.get('title', '')[:50]}' videos={len(videos)}")
            return {
                "playlist_id": data.get("id"),
                "playlist_title": data.get("title"),
                "playlist_url": url,
                "playlist_description": data.get("description"),
                "playlist_uploader": data.get("uploader"),
                "playlist_uploader_id": data.get("uploader_id"),
                "playlist_count": data.get("playlist_count"),
                "total_videos": len(videos),
                "videos": videos,
            }
        except orjson.JSONDecodeError as e:
            log.info(f"[yt-dlp:playlist] JSON_ERROR id={playlist_id}")
            return {"error": f"JSON parse error: {e}", "videos": []}

    async def extract_channel(
        self,
        channel_id: str,
        max_videos: int = 0,
    ) -> dict:
        """Extract channel with FULL metadata for all videos."""
        # Dynamic timeout: 10s per video, min 120s for channels, max 1800s (30min)
        timeout = min(max(max_videos * 10, 120), 1800) if max_videos > 0 else 1800
        log.info(f"[yt-dlp:channel] extracting id={channel_id} max_videos={max_videos} timeout={timeout}s")
        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}/videos"
        else:
            url = f"https://www.youtube.com/@{channel_id}/videos"

        args = [
            *self.BASE_ARGS,
            "--dump-single-json",  # Full metadata
            url,
        ]
        if max_videos > 0:
            args.extend(["--playlist-end", str(max_videos)])

        async with self.semaphore:
            success, stdout, stderr = await self._run_yt_dlp(args, timeout=timeout)
        if not success:
            log.info(f"[yt-dlp:channel] FAILED id={channel_id}")
            return {"error": stderr, "videos": []}
        try:
            data = orjson.loads(stdout)
            entries = data.get("entries", [])
            videos = [self._normalize_video(e) for e in entries if e]
            log.info(f"[yt-dlp:channel] OK id={channel_id} name='{data.get('channel', '')[:50]}' videos={len(videos)}")
            return {
                "channel_id": data.get("channel_id") or data.get("id"),
                "channel_name": data.get("channel") or data.get("uploader"),
                "channel_url": data.get("channel_url") or url,
                "channel_description": data.get("description"),
                "channel_follower_count": data.get("channel_follower_count"),
                "total_videos": len(videos),
                "videos": videos,
            }
        except orjson.JSONDecodeError as e:
            log.info(f"[yt-dlp:channel] JSON_ERROR id={channel_id}")
            return {"error": f"JSON parse error: {e}", "videos": []}

    def _normalize_video(self, data: dict) -> dict:
        """
        Normalize video metadata to consistent schema.
        Extracts ALL available fields from yt-dlp output.
        """
        if not data:
            return {}
        # Extract thumbnails (get highest quality)
        thumbnails = data.get("thumbnails", [])
        thumbnail_url = ""
        if thumbnails:
            # Sort by resolution and get the best one
            sorted_thumbs = sorted(
                thumbnails,
                key = lambda x: (x.get("height", 0) or 0) * (x.get("width", 0) or 0),
                reverse = True
            )
            thumbnail_url = sorted_thumbs[0].get("url", "") if sorted_thumbs else ""
        # Extract chapters
        chapters = []
        for ch in data.get("chapters", []) or []:
            chapters.append({
                "title": ch.get("title", ""),
                "start_time": ch.get("start_time", 0),
                "end_time": ch.get("end_time", 0),
            })
        # Extract subtitles info
        subtitles = list((data.get("subtitles") or {}).keys())
        auto_captions = list((data.get("automatic_captions") or {}).keys())
        return {
            # Core identifiers
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "fulltitle": data.get("fulltitle", ""),
            "description": data.get("description", ""),
            # URLs
            "webpage_url": data.get("webpage_url", ""),
            "original_url": data.get("original_url", ""),
            "thumbnail_url": thumbnail_url,
            "thumbnails": thumbnails,
            # Channel/Uploader
            "channel": data.get("channel", ""),
            "channel_id": data.get("channel_id", ""),
            "channel_url": data.get("channel_url", ""),
            "channel_follower_count": data.get("channel_follower_count"),
            "channel_is_verified": data.get("channel_is_verified", False),
            "uploader": data.get("uploader", ""),
            "uploader_id": data.get("uploader_id", ""),
            "uploader_url": data.get("uploader_url", ""),
            # Dates
            "upload_date": data.get("upload_date", ""),  # YYYYMMDD
            "timestamp": data.get("timestamp"),  # Unix timestamp
            "release_date": data.get("release_date", ""),
            "release_year": data.get("release_year"),
            "modified_date": data.get("modified_date", ""),
            # Duration
            "duration": data.get("duration"),  # seconds
            "duration_string": data.get("duration_string", ""),
            # Engagement
            "view_count": data.get("view_count"),
            "like_count": data.get("like_count"),
            "dislike_count": data.get("dislike_count"),
            "comment_count": data.get("comment_count"),
            "average_rating": data.get("average_rating"),
            # Classification
            "categories": data.get("categories", []),
            "tags": data.get("tags", []),
            "age_limit": data.get("age_limit", 0),
            "availability": data.get("availability", ""),
            # Live status
            "is_live": data.get("is_live", False),
            "was_live": data.get("was_live", False),
            "live_status": data.get("live_status", ""),
            # Content structure
            "chapters": chapters,
            "heatmap": data.get("heatmap"),
            # Subtitles/Captions
            "subtitles": subtitles,
            "automatic_captions": auto_captions,
            # Playlist context (if part of playlist)
            "playlist": data.get("playlist"),
            "playlist_id": data.get("playlist_id"),
            "playlist_title": data.get("playlist_title"),
            "playlist_index": data.get("playlist_index"),
            "playlist_count": data.get("playlist_count"),
            # Technical
            "extractor": data.get("extractor", ""),
            "extractor_key": data.get("extractor_key", ""),
            # Extraction metadata
            "_extracted_at": datetime.utcnow().isoformat(),
        }


# Global extractor instance
_extractor: Optional[YtDlpExtractor] = None


def get_extractor() -> YtDlpExtractor:
    """Get or create the global extractor instance."""
    global _extractor
    if _extractor is None:
        _extractor = YtDlpExtractor(
            max_concurrent = 10,
            timeout = 60.0,
        )
    return _extractor


# =============================================================================
# Transcription with Proxy Fallback (WARP -> Tor -> Direct)
# =============================================================================
def build_warp_proxy_url() -> str | None:
    """Build WARP proxy URL from environment variables."""
    host = os.environ.get("WARP_PROXY_HOST")
    port = os.environ.get("WARP_PROXY_PORT")
    if not host or not port:
        return None
    user = os.environ.get("WARP_PROXY_USER", "")
    password = os.environ.get("WARP_PROXY_PASS", "")
    if user and password:
        # URL-encode user and password to handle special chars like /
        return f"socks5://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
    return f"socks5://{host}:{port}"


def build_tor_proxy_url() -> str | None:
    """Build Tor proxy URL from environment variables."""
    host = os.environ.get("TOR_PROXY_HOST")
    port = os.environ.get("TOR_PROXY_PORT")
    if not host or not port:
        return None
    return f"socks5://{host}:{port}"


def get_proxy_config(proxy_url: str) -> GenericProxyConfig:
    """Create a GenericProxyConfig from a proxy URL."""
    return GenericProxyConfig(
        http_url = proxy_url, 
        https_url = proxy_url)


async def add_transcription(video: dict, use_playwright: bool = True) -> dict:
    """
    Add full transcription to a video metadata dict.
    Uses Playwright by default (bypasses IP blocking), falls back to proxy chain.
    """
    video_id = video.get("id")
    if not video_id:
        return video
    result = await fetch_transcript_with_fallback(video_id, use_playwright=use_playwright)
    if "error" not in result:
        video["transcription"] = result.get("page_content", "")
        video["transcription_language"] = result.get("language", "")
        video["transcription_proxy"] = result.get("proxy_used", "")
        log.info(f"[add_transcription] OK video_id={video_id} lang={video['transcription_language']} method={video['transcription_proxy']}")
    else:
        video["transcription"] = ""
        video["transcription_error"] = result.get("error", "")
        log.info(f"[add_transcription] FAIL video_id={video_id}")
    return video


def fetch_transcript_with_proxy_fallback(
    video_id: str,
    languages: list[str] | None = None
) -> dict:
    """
    Fetch transcript with proxy fallback: WARP -> Tor -> Direct.
    Returns dict with video_id, language, page_content or error.
    """
    # Build proxy chain, skipping unconfigured proxies
    proxy_chain = []
    warp_url = build_warp_proxy_url()
    if warp_url:
        proxy_chain.append(("WARP", warp_url))
    tor_url = build_tor_proxy_url()
    if tor_url:
        proxy_chain.append(("Tor", tor_url))
    proxy_chain.append(("Direct", None))  # Always try direct as fallback

    last_error = None
    for proxy_name, proxy_url in proxy_chain:
        start_time = time.time()
        try:
            log.info(f"[transcript] trying proxy={proxy_name} video_id={video_id}")
            if proxy_url:
                ytt_api = YouTubeTranscriptApi(
                    proxy_config=get_proxy_config(proxy_url)
                )
            else:
                ytt_api = YouTubeTranscriptApi()
            if languages:
                fetched = ytt_api.fetch(video_id, languages=languages)
            else:
                transcript_list = ytt_api.list(video_id)
                first_transcript = next(iter(transcript_list))
                fetched = first_transcript.fetch()
            elapsed = time.time() - start_time
            content = " ".join([snippet.text for snippet in fetched])
            log.info(f"[transcript] OK proxy={proxy_name} video_id={video_id} lang={fetched.language_code} chars={len(content)} time={elapsed:.2f}s")
            return {
                "video_id": video_id,
                "language": fetched.language_code,
                "page_content": content,
                "proxy_used": proxy_name,
            }
        except Exception as e:
            elapsed = time.time() - start_time
            last_error = str(e)
            log.info(f"[transcript] FAIL proxy={proxy_name} video_id={video_id} time={elapsed:.2f}s error={last_error[:100]}")
            continue
    log.info(f"[transcript] ALL_FAILED video_id={video_id}")
    return {
        "video_id": video_id,
        "error": last_error,
    }


# =============================================================================
# Playwright CDP Transcript Extraction (Bypasses IP Blocking)
# =============================================================================
# CDP endpoints for Playwright server (Tailscale addresses)
CDP_HEADLESS = os.environ.get(
    "PLAYWRIGHT_CDP_HEADLESS",
    "https://playwright-cdp-headless.YOUR_TAILNET_DOMAIN.ts.net"
)
CDP_HEADED = os.environ.get(
    "PLAYWRIGHT_CDP_HEADED",
    "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"
)


@dataclass
class TranscriptSegment:
    timestamp: str
    text: str


async def fetch_transcript_with_playwright(
    video_id: str,
    headless: bool = True,
    timeout_ms: int = 15000,
) -> dict:
    """
    Fetch transcript using Playwright CDP connection (bypasses IP blocking).

    Args:
        video_id: YouTube video ID
        headless: Use headless browser (faster) or headed (visible in noVNC)
        timeout_ms: Timeout for waiting for transcript panel

    Returns:
        dict with video_id, language, page_content, segments or error
    """
    cdp_url = CDP_HEADLESS if headless else CDP_HEADED
    url = f"https://www.youtube.com/watch?v={video_id}"
    start_time = time.time()
    log.info(f"[playwright:transcript] starting video_id={video_id} cdp={cdp_url}")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
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
                timeout = timeout_ms,
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
    except Exception as e:
        elapsed = time.time() - start_time
        log.info(f"[playwright:transcript] FAIL video_id={video_id} time={elapsed:.2f}s error={str(e)[:100]}")
        return {
            "video_id": video_id,
            "error": str(e),
        }
    if not raw_text:
        elapsed = time.time() - start_time
        log.info(f"[playwright:transcript] NO_TRANSCRIPT video_id={video_id} time={elapsed:.2f}s")
        return {
            "video_id": video_id,
            "error": "No transcript available",
        }
    # Parse transcript
    segments = _parse_playwright_transcript(raw_text)
    page_content = " ".join([seg.text for seg in segments])
    elapsed = time.time() - start_time
    log.info(f"[playwright:transcript] OK video_id={video_id} segments={len(segments)} chars={len(page_content)} time={elapsed:.2f}s")
    return {
        "video_id": video_id,
        "language": "auto",  # Playwright doesn't expose language info easily
        "page_content": page_content,
        "segments": [{"timestamp": s.timestamp, "text": s.text} for s in segments],
        "proxy_used": "Playwright",
    }


def _parse_playwright_transcript(raw_text: str) -> list[TranscriptSegment]:
    """Parse raw transcript text from Playwright into segments."""
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


async def fetch_transcript_with_fallback(
    video_id: str,
    languages: list[str] | None = None,
    use_playwright: bool = True,
) -> dict:
    """
    Fetch transcript using Playwright CDP (bypasses IP blocking).
    Proxy fallback disabled - Playwright should work 100% of the time.

    Args:
        video_id: YouTube video ID
        languages: Preferred languages (not used with Playwright)
        use_playwright: Use Playwright (default True, set False to use proxy chain)
    """
    if use_playwright:
        return await fetch_transcript_with_playwright(video_id, headless=True)
    # Legacy proxy chain (only if explicitly requested)
    result = await asyncio.to_thread(
        fetch_transcript_with_proxy_fallback,
        video_id,
        languages
    )
    return result


# =============================================================================
# ElasticSearch Indexing
# =============================================================================
ES_INDEX_YOUTUBE = "coelhonexus-youtube"


async def index_videos_to_elasticsearch(
    es_client,
    videos: list[dict],
    index: str = ES_INDEX_YOUTUBE,
) -> dict:
    """
    Index videos to ElasticSearch in bulk.
    Returns summary of indexed/failed documents.
    """
    if not videos:
        log.info(f"[elasticsearch] skip indexing, no videos")
        return {"indexed": 0, "failed": 0}
    # Build bulk operations
    operations = []
    for video in videos:
        video_id = video.get("id")
        if not video_id:
            continue
        # Index operation
        operations.append({"index": {"_index": index, "_id": video_id}})
        operations.append(video)
    if not operations:
        log.info(f"[elasticsearch] skip indexing, no valid video IDs")
        return {"indexed": 0, "failed": 0}
    # Execute bulk
    log.info(f"[elasticsearch] indexing {len(operations)//2} videos to {index}")
    start_time = time.time()
    try:
        response = await es_client.bulk(operations=operations, refresh=True)
        elapsed = time.time() - start_time
        indexed = sum(1 for item in response["items"] if item["index"]["status"] in (200, 201))
        failed = len(response["items"]) - indexed
        log.info(f"[elasticsearch] OK indexed={indexed} failed={failed} time={elapsed:.2f}s")
        return {"indexed": indexed, "failed": failed, "errors": response.get("errors", False)}
    except Exception as e:
        elapsed = time.time() - start_time
        log.info(f"[elasticsearch] ERROR time={elapsed:.2f}s error={str(e)[:200]}")
        return {"indexed": 0, "failed": len(videos), "error": str(e)}


async def create_youtube_index(es_client, index: str = ES_INDEX_YOUTUBE):
    """Create ElasticSearch index with optimal mappings for YouTube videos."""
    mapping = {
        "mappings": {
            "properties": {
                # Core fields
                "id": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "standard"},
                "fulltitle": {"type": "text"},
                "description": {"type": "text", "analyzer": "standard"},

                # URLs
                "webpage_url": {"type": "keyword"},
                "thumbnail_url": {"type": "keyword"},

                # Channel
                "channel": {"type": "text"},
                "channel_id": {"type": "keyword"},
                "channel_url": {"type": "keyword"},
                "channel_follower_count": {"type": "long"},
                "channel_is_verified": {"type": "boolean"},
                "uploader": {"type": "text"},
                "uploader_id": {"type": "keyword"},

                # Dates
                "upload_date": {"type": "keyword"},  # YYYYMMDD format
                "timestamp": {"type": "date", "format": "epoch_second"},
                "release_date": {"type": "keyword"},

                # Duration
                "duration": {"type": "integer"},
                "duration_string": {"type": "keyword"},

                # Engagement
                "view_count": {"type": "long"},
                "like_count": {"type": "long"},
                "dislike_count": {"type": "long"},
                "comment_count": {"type": "long"},
                "average_rating": {"type": "float"},

                # Classification
                "categories": {"type": "keyword"},
                "tags": {"type": "keyword"},
                "age_limit": {"type": "integer"},
                "availability": {"type": "keyword"},

                # Live
                "is_live": {"type": "boolean"},
                "was_live": {"type": "boolean"},
                "live_status": {"type": "keyword"},

                # Chapters
                "chapters": {
                    "type": "nested",
                    "properties": {
                        "title": {"type": "text"},
                        "start_time": {"type": "float"},
                        "end_time": {"type": "float"},
                    }
                },

                # Subtitles
                "subtitles": {"type": "keyword"},
                "automatic_captions": {"type": "keyword"},

                # Transcription
                "transcription": {"type": "text", "analyzer": "standard"},
                "transcription_language": {"type": "keyword"},

                # Extraction metadata
                "_extracted_at": {"type": "date"},
            }
        },
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        }
    }

    # Create index if not exists
    try:
        exists = await es_client.indices.exists(index=index)
        if not exists:
            await es_client.indices.create(
                index=index,
                mappings=mapping["mappings"],
                settings=mapping["settings"],
            )
            return {"created": True, "index": index}
        return {"created": False, "index": index, "message": "Index already exists"}
    except Exception as e:
        return {"created": False, "index": index, "error": str(e)}
