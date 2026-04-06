"""
YouTube helpers:
- yt-dlp subprocess for metadata extraction (optimized for speed and completeness)
- Playwright CDP for transcript extraction (optimized v4 - smart waits)
- ElasticSearch indexing

Playwright optimizations v4:
- Smart element waiting (no fixed timeouts)
- networkidle navigation for full page load
- Polling loop for transcript panel
- Larger viewport (1920x1080) to avoid mobile UI
- Periodic browser recreation (prevents stale CDP connections)
- Aggressive resource blocking (video, ads, tracking)
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
from urllib.parse import urlparse
from urllib.request import urlopen
import ssl
import json
from playwright.async_api import async_playwright

# Use uvicorn's logger for proper output in FastAPI
log = logging.getLogger("uvicorn.error")

# ElasticSearch index names (normalized: metadata + transcriptions)
ES_INDEX_METADATA = "coelhonexus-youtube-metadata"
ES_INDEX_TRANSCRIPTIONS = "coelhonexus-youtube-transcriptions"


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
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
                limit = self.buffer_limit,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout = effective_timeout
            )
            elapsed = time.time() - start_time
            success = proc.returncode == 0
            stdout_str = stdout.decode("utf-8", errors = "replace")
            stderr_str = stderr.decode("utf-8", errors = "replace")
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
        # Duration filters
        duration: str | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        # Date filters
        date_after: str | None = None,
        date_before: str | None = None,
        # View/like count filters
        min_views: int | None = None,
        max_views: int | None = None,
        min_likes: int | None = None,
        # Live status filters
        is_live: bool | None = None,
        live_status: str | None = None,
        # Availability filter
        availability: str | None = None,
        # Age limit filter
        age_limit: int | None = None,
        # String filters (support operators: *=, ^=, $=, ~=)
        title_contains: str | None = None,
        description_contains: str | None = None,
        channel_name: str | None = None,
    ) -> list[dict]:
        """
        Search YouTube and return available metadata from search results.
        Fast: uses --flat-playlist (no per-video extraction).

        All filters applied via yt-dlp post-processing:
        - duration: preset or custom min/max in seconds
        - date_after/date_before: YYYYMMDD or relative (e.g., "today-2weeks")
        - min_views/max_views: View count range
        - min_likes: Minimum like count
        - is_live/live_status: Live stream filtering
        - availability: public, unlisted, premium_only, subscriber_only
        - age_limit: Download only videos suitable for given age
        - title_contains/description_contains/channel_name: String filters with operators

        String filter operators:
        - Exact: "Python Tutorial"
        - Contains: "*=tutorial"
        - Starts with: "^=How to"
        - Ends with: "$=2026"
        - Regex: "~=(?i)python"

        Returns list of dicts with: id, title, url, duration, channel, view_count, etc.
        """
        # Check if any filters are active
        has_filters = any([
            duration, duration_min, duration_max, date_after, date_before,
            min_views, max_views, min_likes, is_live, live_status,
            availability, age_limit, title_contains, description_contains, channel_name
        ])
        # Request more results to account for post-filtering
        fetch_count = max_results * 3 if has_filters else max_results
        # Use ytsearchdate prefix for date sorting
        prefix = "ytsearchdate" if sort_by_date else "ytsearch"
        search_url = f"{prefix}{fetch_count}:{query}"
        # Build match-filter conditions (combined with & for AND logic)
        match_conditions = []
        # Duration filters
        if duration_min is not None or duration_max is not None:
            # Custom duration range overrides preset
            if duration_min is not None:
                match_conditions.append(f"duration>={duration_min}")
            if duration_max is not None:
                match_conditions.append(f"duration<={duration_max}")
        elif duration:
            # Preset duration ranges
            if duration == "Under 4 minutes":
                match_conditions.append("duration<240")
            elif duration == "4 - 20 minutes":
                match_conditions.append("duration>=240")
                match_conditions.append("duration<=1200")
            elif duration == "Over 20 minutes":
                match_conditions.append("duration>1200")
        # View count filters (using match-filter, not deprecated --min/max-views)
        if min_views is not None:
            match_conditions.append(f"view_count>=?{min_views}")
        if max_views is not None:
            match_conditions.append(f"view_count<=?{max_views}")
        # Like count filter
        if min_likes is not None:
            match_conditions.append(f"like_count>=?{min_likes}")
        # Live status filters
        if live_status:
            match_conditions.append(f"live_status='{live_status}'")
        elif is_live is True:
            match_conditions.append("is_live")
        elif is_live is False:
            match_conditions.append("!is_live")
        # Availability filter
        if availability:
            match_conditions.append(f"availability='{availability}'")
        # String filters (check for operators, default to contains)
        def build_string_filter(field: str, value: str) -> str:
            # Support operators: *=, ^=, $=, ~= and their negations !*=, !^=, !$=, !~=
            if value.startswith(("*=", "^=", "$=", "~=", "!*=", "!^=", "!$=", "!~=", "=")):
                return f"{field}{value}"
            # Default: contains (case-insensitive via regex)
            return f"{field}*='{value}'"
        if title_contains:
            match_conditions.append(build_string_filter("title", title_contains))
        if description_contains:
            match_conditions.append(build_string_filter("description", description_contains))
        if channel_name:
            match_conditions.append(build_string_filter("channel", channel_name))
        log.info(f"[yt-dlp:search] query='{query}' max={max_results} sort_date={sort_by_date} filters={len(match_conditions)}")
        args = [
            *self.BASE_ARGS,
            "--flat-playlist",
            "--dump-single-json",
            # Enable approximate date for flat-playlist filtering
            "--extractor-args", "youtube:approximate_date",
        ]
        # Add combined match-filter (all conditions with AND logic)
        if match_conditions:
            combined_filter = " & ".join(match_conditions)
            args.extend(["--match-filter", combined_filter])
        # Date filters (dedicated args, not match-filter)
        if date_after:
            args.extend(["--dateafter", date_after])
        if date_before:
            args.extend(["--datebefore", date_before])
        # Age limit filter (dedicated arg)
        if age_limit is not None:
            args.extend(["--age-limit", str(age_limit)])
        args.append(search_url)
        async with self.semaphore:
            success, stdout, stderr = await self._run_yt_dlp(args, timeout=90)
        if not success:
            log.info(f"[yt-dlp:search] FAILED query='{query}'")
            return [{"error": stderr or "Search failed"}]
        try:
            data = orjson.loads(stdout)
            entries = data.get("entries", [])
            # Return all available metadata from search results (limited to max_results)
            videos = []
            for entry in entries:
                if entry and entry.get("id"):
                    videos.append({
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}",
                        "duration": entry.get("duration"),
                        "duration_string": entry.get("duration_string"),
                        "view_count": entry.get("view_count"),
                        "like_count": entry.get("like_count"),
                        "channel": entry.get("channel"),
                        "channel_id": entry.get("channel_id"),
                        "channel_url": entry.get("channel_url"),
                        "thumbnail": entry.get("thumbnail"),
                        "description": entry.get("description"),
                        "upload_date": entry.get("upload_date"),
                        "live_status": entry.get("live_status"),
                        "availability": entry.get("availability"),
                    })
                    if len(videos) >= max_results:
                        break
            log.info(f"[yt-dlp:search] OK query='{query}' results={len(videos)}")
            return videos
        except orjson.JSONDecodeError:
            log.info(f"[yt-dlp:search] JSON_ERROR query='{query}'")
            return [{"error": "JSON parse error"}]

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
# Batch Transcription Helper with ES Caching
# =============================================================================
# NOTE: yt-dlp subtitle extraction removed - always fails with 429 rate limit
# Playwright CDP is now the only transcript extraction method


async def _check_existing_transcriptions(
    es_client,
    video_ids: list[str],
    languages: list[str] | None = None,
) -> dict[str, set[str]]:
    """
    Check ElasticSearch transcriptions index for existing transcriptions.

    Returns:
        Dict mapping video_id -> set of existing language codes
    """
    if not es_client or not video_ids:
        return {}
    try:
        # Query transcriptions index for all matching video_ids
        result = await es_client.search(
            index = ES_INDEX_TRANSCRIPTIONS,
            query = {"terms": {"video_id": video_ids}},
            _source = ["video_id", "lang"],
            size = len(video_ids) * 10,  # Allow up to 10 languages per video
        )
        existing = {}
        for hit in result.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            vid = source.get("video_id")
            lang = source.get("lang")
            if vid and lang:
                if vid not in existing:
                    existing[vid] = set()
                existing[vid].add(lang)
        return existing
    except Exception as e:
        log.warning(f"[transcription-cache] ES lookup failed: {e}")
        return {}


def _needs_transcription(
    existing_langs: set[str],
    languages: list[str] | None = None,
) -> bool:
    """
    Check if we need to fetch transcription based on existing languages.

    Args:
        existing_langs: Set of existing language codes (e.g., {"en", "pt"})
        languages: Requested languages (None = any language is fine)

    Returns:
        True if transcription fetch is needed
    """
    if not existing_langs:
        return True
    if languages is None:
        # No specific language requested - any existing transcription is fine
        return False
    # Check if all requested languages exist
    for lang in languages:
        # Match language prefix (e.g., "en" matches "en-US", "en-GB")
        found = any(
            existing_lang.startswith(lang) or lang.startswith(existing_lang)
            for existing_lang in existing_langs
        )
        if not found:
            return True
    return False


async def fetch_transcriptions_batch(
    video_ids: list[str],
    transcript_service = None,
    es_client = None,
    languages: list[str] | None = None,
    chunk_size: int = 10,
) -> list[dict]:
    """
    Fetch transcriptions for videos with ES caching and chunked processing.

    Strategy:
    1. ES cache lookup - skip videos with existing transcriptions
    2. Chunk processing - process in batches of chunk_size for crash resilience
    3. Playwright CDP - browser-based DOM scraping (optimized v5)

    For large batches (e.g., 500 videos overnight):
    - Processes in chunks of 10 videos (frequent ES checkpoints)
    - Each chunk is independent - crash only loses current chunk (~15 max)
    - On retry, ES cache skips already-indexed videos

    Args:
        video_ids: List of YouTube video IDs
        transcript_service: PlaywrightTranscriptService instance (uses global if None)
        es_client: AsyncElasticsearch client for cache lookup (optional)
        languages: Requested languages (None = best available, English priority)
        chunk_size: Videos per chunk (default 50 - optimal for memory/resilience)

    Returns:
        List of transcription documents ready for ES indexing:
        [{"video_id": "abc", "lang": "en", "content": "...", "is_auto": False, "method": "playwright"}, ...]
    """
    if not video_ids:
        return []

    # Check ES cache for existing transcriptions
    existing_transcriptions = await _check_existing_transcriptions(
        es_client,
        video_ids,
        languages)

    # Filter out videos that already have required transcriptions
    ids_to_fetch = []
    cached_count = 0
    for vid in video_ids:
        existing_langs = existing_transcriptions.get(vid, set())
        if _needs_transcription(existing_langs, languages):
            ids_to_fetch.append(vid)
        else:
            cached_count += 1
            log.info(f"[transcription-cache] HIT {vid} langs={existing_langs}")

    if cached_count > 0:
        log.info(f"[fetch_transcriptions_batch] Cache: {cached_count} hits, {len(ids_to_fetch)} to fetch")

    if not ids_to_fetch:
        log.info("[fetch_transcriptions_batch] All videos cached, no fetch needed")
        return []

    # Fetch via Playwright CDP (only method - yt-dlp subtitle extraction removed due to 429 rate limits)
    service = transcript_service or _transcript_service
    if not service or not service._initialized:
        log.error("[fetch_transcriptions_batch] Playwright service not available")
        return []

    # Chunk processing for large batches (crash resilience)
    total_to_fetch = len(ids_to_fetch)
    num_chunks = (total_to_fetch + chunk_size - 1) // chunk_size
    log.info(f"[fetch_transcriptions_batch] Fetching {total_to_fetch} videos in {num_chunks} chunks of {chunk_size}")

    transcription_docs = []
    total_success = 0
    total_failed = 0

    for chunk_num in range(num_chunks):
        start_idx = chunk_num * chunk_size
        end_idx = min(start_idx + chunk_size, total_to_fetch)
        chunk_ids = ids_to_fetch[start_idx:end_idx]

        log.info(f"[fetch_transcriptions_batch] Chunk {chunk_num + 1}/{num_chunks}: {len(chunk_ids)} videos")

        # Process chunk
        chunk_results = await service.fetch_batch(chunk_ids, prefer_manual=True)

        # Process chunk results - collect docs for this chunk separately
        chunk_docs = []
        for result in chunk_results:
            vid = result.get("video_id")
            if not vid:
                continue

            if "error" not in result and result.get("page_content"):
                lang = result.get("language", "unknown")
                content = result.get("page_content", "")
                is_auto = result.get("is_auto_generated", True)
                doc = {
                    "id": f"{vid}_{lang}",
                    "video_id": vid,
                    "lang": lang,
                    "content": content,
                    "is_auto": is_auto,
                    "method": "playwright",
                    "_extracted_at": datetime.utcnow().isoformat(),
                }
                chunk_docs.append(doc)
                transcription_docs.append(doc)
                total_success += 1
                log.info(f"[fetch_transcriptions_batch] OK {vid} lang={lang} auto={is_auto} len={len(content)}")
            else:
                total_failed += 1
                log.warning(f"[fetch_transcriptions_batch] FAIL {vid}: {result.get('error', '')[:100]}")

        # Index chunk results immediately (crash resilience - don't lose progress)
        if chunk_docs and es_client:
            try:
                await index_transcriptions_to_elasticsearch(es_client, chunk_docs)
                log.info(f"[fetch_transcriptions_batch] Chunk {chunk_num + 1} indexed: {len(chunk_docs)} docs")
            except Exception as e:
                log.error(f"[fetch_transcriptions_batch] Chunk {chunk_num + 1} index error: {e}")

        log.info(f"[fetch_transcriptions_batch] Chunk {chunk_num + 1}/{num_chunks} complete: "
                 f"{total_success} OK, {total_failed} failed so far")

    log.info(f"[fetch_transcriptions_batch] Complete: {total_success}/{total_to_fetch} fetched, "
             f"{total_failed} failed, {cached_count} cached")
    return transcription_docs


# =============================================================================
# Playwright CDP Transcript Extraction
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

# Resource blocking patterns - balanced for speed and reliability
BLOCK_PATTERNS = [
    # VIDEO/AUDIO STREAMING (Biggest speedup: 2-5 seconds)
    "**/videoplayback*",
    "**/googlevideo.com/*",
    "**/*.googlevideo.com/*",
    # ADS
    "**/doubleclick.net/*",
    "**/googleadservices.com/*",
    "**/googlesyndication.com/*",
    "**/googleads.g.doubleclick.net/*",
    "**/youtube.com/pagead/*",
    # ANALYTICS/TRACKING
    "**/google-analytics.com/*",
    "**/googletagmanager.com/*",
    "**/youtube.com/api/stats/*",
    "**/youtube.com/ptracking*",
    "**/s.youtube.com/*",
]

BLOCK_RESOURCE_TYPES = {"media"}  # Only block media (video/audio)


@dataclass
class TranscriptSegment:
    timestamp: str
    text: str


@dataclass
class CaptionTrack:
    language_code: str
    name: str
    is_auto_generated: bool
    base_url: str


def _get_cdp_websocket_url(cdp_endpoint: str) -> str:
    """
    Get the proper WebSocket URL for CDP connection.
    Handles HTTPS reverse proxy (Tailscale Ingress) by constructing wss:// URL.
    """
    parsed = urlparse(cdp_endpoint)
    json_url = f"{cdp_endpoint}/json/version"
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(json_url, timeout = 10, context = ctx) as response:
            data = json.loads(response.read().decode())
            ws_url = data.get("webSocketDebuggerUrl", "")
            if not ws_url:
                log.warning(f"[cdp] No webSocketDebuggerUrl in response from {json_url}")
                return cdp_endpoint
            ws_parsed = urlparse(ws_url)
            ws_path = ws_parsed.path
            if parsed.scheme == "https":
                proper_url = f"wss://{parsed.netloc}{ws_path}"
            else:
                proper_url = f"ws://{parsed.netloc}{ws_path}"
            log.info(f"[cdp] Resolved: {proper_url[:60]}...")
            return proper_url
    except Exception as e:
        log.warning(f"[cdp] Failed to fetch {json_url}: {e}")
        return cdp_endpoint


async def _setup_routes(page) -> None:
    """Set up aggressive resource blocking."""
    for pattern in BLOCK_PATTERNS:
        await page.route(pattern, lambda r: r.abort())
    async def block_by_type(route):
        if route.request.resource_type in BLOCK_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    await page.route("**/*", block_by_type)


async def _kill_youtube_background(page) -> None:
    """Kill YouTube's resource-hungry background processes."""
    await page.evaluate('''
        () => {
            const video = document.querySelector("video");
            if (video) {
                video.pause();
                video.removeAttribute("src");
                video.load();
            }
            const highestId = window.setTimeout(() => {}, 0);
            for (let i = 0; i < highestId; i++) {
                window.clearTimeout(i);
                window.clearInterval(i);
            }
        }
    ''')


async def _get_caption_tracks(page) -> list[CaptionTrack]:
    """Extract caption tracks from ytInitialPlayerResponse."""
    tracks_data = await page.evaluate('''
        () => {
            const caps = window.ytInitialPlayerResponse?.captions;
            if (!caps?.playerCaptionsTracklistRenderer?.captionTracks) return [];
            return caps.playerCaptionsTracklistRenderer.captionTracks.map(t => ({
                languageCode: t.languageCode || '',
                name: t.name?.simpleText || t.languageCode || '',
                isAutoGenerated: t.kind === 'asr' || (t.vssId || '').startsWith('a.'),
                baseUrl: t.baseUrl || ''
            }));
        }
    ''')
    return [
        CaptionTrack(
            language_code = t['languageCode'],
            name = t['name'],
            is_auto_generated = t['isAutoGenerated'],
            base_url = t['baseUrl']
        )
        for t in tracks_data
    ]


def _select_best_track(tracks: list[CaptionTrack], prefer_manual: bool = True) -> CaptionTrack:
    """Select best track: English manual > Portuguese manual > any manual > English auto > any."""
    def priority(t: CaptionTrack) -> tuple:
        is_english = t.language_code.startswith('en')
        is_portuguese = t.language_code.startswith('pt')
        return (
            t.is_auto_generated if prefer_manual else False,
            0 if is_english else (1 if is_portuguese else 2),
        )
    return sorted(tracks, key = priority)[0]


async def _fetch_transcript_direct(page, base_url: str) -> list[dict]:
    """Try to fetch transcript directly from caption URL (fast path)."""
    json_url = base_url + ("&" if "?" in base_url else "?") + "fmt=json3"
    result = await page.evaluate(f'''
        async () => {{
            try {{
                const resp = await fetch("{json_url}", {{
                    credentials: "include",
                    headers: {{ "Accept": "application/json" }}
                }});
                if (!resp.ok) {{
                    return {{ error: "HTTP " + resp.status }};
                }}
                const text = await resp.text();
                if (!text || text.length === 0) {{
                    return {{ error: "empty response" }};
                }}
                if (text.startsWith('<')) {{
                    return {{ error: "blocked (HTML response)" }};
                }}
                if (text.length < 10) {{
                    return {{ error: "truncated response: " + text.length + " bytes" }};
                }}
                try {{
                    return JSON.parse(text);
                }} catch (parseErr) {{
                    return {{ error: "JSON parse failed: " + parseErr.message + " (len=" + text.length + ")" }};
                }}
            }} catch (e) {{
                return {{ error: e.message }};
            }}
        }}
    ''')
    if 'error' in result:
        raise ValueError(result['error'])
    segments = []
    for event in result.get('events', []):
        if 'segs' in event:
            text = ''.join(s.get('utf8', '') for s in event['segs']).strip()
            if text:
                start_ms = event.get('tStartMs', 0)
                minutes = start_ms // 60000
                seconds = (start_ms // 1000) % 60
                segments.append({'timestamp': f"{minutes}:{seconds:02d}", 'text': text})
    return segments


async def _extract_via_dom(page, timeout_ms: int) -> str:
    """
    Extract transcript via DOM interaction (optimized v4 with smart waits).

    Strategy:
    1. Wait for video player to be ready (indicates page loaded)
    2. Wait for and click expand button (or skip if not found)
    3. Wait for and click transcript button
    4. Poll for transcript panel to load (up to 15 attempts)
    5. Extract content
    """
    # Step 1: Wait for YouTube to fully render its UI
    try:
        # Wait for video player (indicates core UI loaded)
        await page.wait_for_selector('#movie_player, ytd-player', state='attached', timeout=15000)
        # Also wait for description area (where transcript button lives)
        await page.wait_for_selector('ytd-watch-metadata, #above-the-fold', state='attached', timeout=10000)
        log.info("[dom] Page ready (player + metadata loaded)")
    except Exception:
        # Fallback: wait a bit for JS to render
        await page.wait_for_timeout(3000)
        log.info("[dom] Fallback wait completed")

    # Check if transcript panel is already visible
    already_visible = await page.evaluate('''() => {
        const segments = document.querySelectorAll('transcript-segment-view-model, ytd-transcript-segment-renderer');
        if (segments.length > 0) return true;
        const panel = document.querySelector('ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]');
        return panel && /\\d+:\\d{2}/.test(panel.innerText);
    }''')
    if already_visible:
        log.info("[dom] Transcript panel already visible")
        return await _extract_transcript_text(page)

    # Step 2: Wait for and click expand button (with retry)
    expanded = False
    for expand_attempt in range(3):
        try:
            expand_btn = await page.wait_for_selector(
                'tp-yt-paper-button#expand:not([hidden])',
                state='visible',
                timeout=5000
            )
            if expand_btn:
                await expand_btn.scroll_into_view_if_needed()
                await expand_btn.click()
                log.info("[dom] Description expanded")
                # Wait for transcript section to appear
                await page.wait_for_selector(
                    'ytd-video-description-transcript-section-renderer',
                    state='attached',
                    timeout=5000
                )
                expanded = True
                break
        except Exception:
            if expand_attempt < 2:
                await page.wait_for_timeout(1000)  # Brief wait before retry
            continue

    if not expanded:
        log.info("[dom] Expand button not found after retries, continuing...")

    # Step 3: Find and click transcript button with multiple selectors
    transcript_clicked = False
    selectors = [
        '[aria-label="Show transcript"]',
        'ytd-video-description-transcript-section-renderer button',
        'button[aria-label*="transcript" i]',
    ]

    for selector in selectors:
        try:
            btn = await page.wait_for_selector(selector, state='visible', timeout=3000)
            if btn:
                await btn.scroll_into_view_if_needed()
                await btn.click()
                log.info(f"[dom] Transcript button clicked: {selector}")
                transcript_clicked = True
                break
        except Exception:
            continue

    if not transcript_clicked:
        # Debug info before failing
        debug_info = await page.evaluate('''() => ({
            hasExpandBtn: !!document.querySelector('tp-yt-paper-button#expand'),
            hasTranscriptSection: !!document.querySelector('ytd-video-description-transcript-section-renderer'),
            hasShowTranscriptBtn: !!document.querySelector('[aria-label="Show transcript"]'),
            descExpanded: document.querySelector('ytd-text-inline-expander')?.hasAttribute('is-expanded'),
            url: window.location.href,
        })''')
        log.warning(f"[dom] Transcript button not found. Debug: {debug_info}")
        raise ValueError("Transcript button not found")

    # Step 4: Poll for transcript panel to load (up to 15 attempts, 1s apart)
    panel_loaded = False
    segment_count = 0

    for attempt in range(15):
        panel_state = await page.evaluate('''() => {
            // Check for segments (most reliable)
            const segments = document.querySelectorAll(
                'ytd-transcript-segment-renderer, transcript-segment-view-model'
            );
            if (segments.length > 0) {
                return { loaded: true, segmentCount: segments.length };
            }
            // Check for panel with timestamps
            const panel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
            );
            if (panel && /\\d+:\\d{2}/.test(panel.innerText)) {
                return { loaded: true, segmentCount: 0 };
            }
            // Check new panel by target-id
            const newPanel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]'
            );
            if (newPanel && /\\d+:\\d{2}/.test(newPanel.innerText)) {
                return { loaded: true, segmentCount: 0 };
            }
            return { loaded: false, segmentCount: 0 };
        }''')

        if panel_state.get('loaded'):
            panel_loaded = True
            segment_count = panel_state.get('segmentCount', 0)
            log.info(f"[dom] Panel loaded (attempt {attempt + 1}) segments={segment_count}")
            break

        if attempt < 14:  # Don't sleep on last attempt
            await page.wait_for_timeout(1000)

    if not panel_loaded:
        log.warning("[dom] Panel not loaded after 15 attempts")
        raise ValueError("Transcript panel not loaded")

    return await _extract_transcript_text(page)


async def _extract_transcript_text(page) -> str:
    """Extract text from visible transcript panel.

    Updated for YouTube Feb 2026 UI with multiple fallback strategies:
    1. New transcript-segment-view-model elements
    2. New engagement-panel-searchable-transcript panel
    3. Legacy ytd-engagement-panel with visibility attribute
    """
    return await page.evaluate('''
        () => {
            // Method 1: Feb 2026 UI - transcript-segment-view-model with .segment-text
            const segmentTexts = document.querySelectorAll(
                'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"] .segment-text'
            );
            if (segmentTexts.length > 0) {
                const parts = [];
                segmentTexts.forEach(el => {
                    // Get timestamp from sibling or parent
                    const container = el.closest('ytd-transcript-segment-renderer, transcript-segment-view-model');
                    const timestamp = container?.querySelector('.segment-timestamp')?.innerText?.trim() || '';
                    const text = el.innerText?.trim() || '';
                    if (timestamp && text) {
                        parts.push(timestamp + '\\n' + text);
                    } else if (text) {
                        parts.push(text);
                    }
                });
                if (parts.length > 0) return parts.join('\\n');
            }
            // Method 2: Modern transcript-segment-view-model (Apr 2026 UI)
            // Each segment contains timestamp + text in innerText
            const segmentModels = document.querySelectorAll('transcript-segment-view-model');
            if (segmentModels.length > 0) {
                const parts = [];
                segmentModels.forEach(seg => {
                    // Extract timestamp from dedicated element
                    const tsEl = seg.querySelector('[class*="Timestamp"], .ytwTranscriptSegmentTimestampContainer div');
                    const textEl = seg.querySelector('.yt-core-attributed-string, [class*="Text"]');
                    const timestamp = tsEl?.innerText?.trim() || '';
                    const text = textEl?.innerText?.trim() || '';
                    if (timestamp && text) {
                        parts.push(timestamp + '\\n' + text);
                    } else if (seg.innerText) {
                        // Fallback: use full innerText (includes timestamp)
                        parts.push(seg.innerText.trim());
                    }
                });
                if (parts.length > 0) return parts.join('\\n');
            }
            // Method 3: New panel by target-id
            const newPanel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]'
            );
            if (newPanel && /\\d+:\\d{2}/.test(newPanel.innerText)) {
                return newPanel.innerText;
            }
            // Method 4: Legacy - old visibility attribute
            const panels = document.querySelectorAll('ytd-engagement-panel-section-list-renderer');
            for (const p of panels) {
                if (p.getAttribute('visibility') === 'ENGAGEMENT_PANEL_VISIBILITY_EXPANDED'
                    && /\\d+:\\d{2}/.test(p.innerText)) {
                    return p.innerText;
                }
            }
            return '';
        }
    ''')


def _parse_transcript(raw_text: str) -> list[TranscriptSegment]:
    """Parse raw transcript text into segments."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    segments = []
    i = 0
    while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
        i += 1
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\d+:\d{2}$", line):
            timestamp = line
            i += 1
            if i < len(lines) and re.match(r"^\d+\s+(second|minute)", lines[i]):
                i += 1
            text_parts = []
            while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
                text_parts.append(lines[i])
                i += 1
            if text_parts:
                segments.append(TranscriptSegment(timestamp=timestamp, text=" ".join(text_parts)))
        else:
            i += 1
    return segments


# =============================================================================
# PlaywrightTranscriptService - Browser Pool with Semaphore Control
# =============================================================================
class PlaywrightTranscriptService:
    """
    Browser pool with semaphore-controlled concurrency for transcript extraction.

    Features:
    - Tenacity-based retry with exponential backoff
    - Semaphore limits concurrent browser contexts (default: 5)
    - Context pool for reuse (reduces context creation overhead)
    - Memory-safe: proper cleanup in all paths

    Usage:
        # Initialize at FastAPI lifespan
        service = PlaywrightTranscriptService(max_concurrent=5)
        await service.initialize()

        # Use in endpoint
        results = await service.fetch_batch(video_ids)

        # Cleanup at shutdown
        await service.close()
    """

    def __init__(
        self,
        cdp_url: str | None = None,
        max_concurrent: int = 5,
        context_pool_size: int | None = None,
        timeout_ms: int = 30000,
        navigation_timeout_ms: int = 60000,
        browser_refresh_interval: int = 15,  # Recreate browser every N videos
        max_retries: int = 2,  # Max retries per video with exponential backoff
    ):
        """
        Args:
            cdp_url: WebSocket URL for CDP connection (auto-resolved if None)
            max_concurrent: Maximum parallel transcript extractions
            context_pool_size: Number of warm contexts to keep in pool (defaults to max_concurrent)
            timeout_ms: Timeout for DOM scraping fallback
            navigation_timeout_ms: Timeout for Page.goto (default: 60s)
            browser_refresh_interval: Recreate browser connection every N videos (prevents stale CDP)
            max_retries: Max retries per video with exponential backoff
        """
        self._cdp_endpoint = cdp_url
        self.max_concurrent = max_concurrent
        # Pool size should match max_concurrent to avoid context creation storms
        self.context_pool_size = context_pool_size if context_pool_size is not None else max_concurrent
        self.timeout_ms = timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms
        self.browser_refresh_interval = browser_refresh_interval
        self.max_retries = max_retries
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._playwright = None
        self._browser = None
        self._context_pool: asyncio.Queue = None
        self._initialized = False
        self._cdp_url = None
        # Browser refresh tracking
        self._videos_since_refresh = 0
        self._refresh_lock = asyncio.Lock()
        self._total_extractions = 0
        self._total_errors = 0
        # Active operations counter - prevents refresh during in-flight requests
        self._active_ops = 0
        self._active_ops_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize browser and context pool. Call once at startup."""
        if self._initialized:
            return
        # Resolve CDP WebSocket URL (use HEADED - YouTube blocks headless for transcripts)
        cdp_endpoint = self._cdp_endpoint or CDP_HEADED
        self._cdp_url = await asyncio.to_thread(_get_cdp_websocket_url, cdp_endpoint)
        log.info(f"[transcript-service] Initializing with CDP: {self._cdp_url[:60]}...")
        # Connect to browser
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)
        # Pre-warm context pool
        self._context_pool = asyncio.Queue(maxsize = self.context_pool_size)
        for i in range(self.context_pool_size):
            ctx = await self._create_context()
            await self._context_pool.put(ctx)
            log.info(f"[transcript-service] Warmed context {i+1}/{self.context_pool_size}")
        self._initialized = True
        log.info(f"[transcript-service] Ready (max_concurrent={self.max_concurrent}, pool_size={self.context_pool_size})")

    async def close(self) -> None:
        """Cleanup all resources. Call at shutdown."""
        if not self._initialized:
            return
        log.info("[transcript-service] Shutting down...")
        # Close all pooled contexts
        closed = 0
        while not self._context_pool.empty():
            try:
                ctx = self._context_pool.get_nowait()
                await ctx.close()
                closed += 1
            except:
                pass
        log.info(f"[transcript-service] Closed {closed} pooled contexts")
        if self._browser:
            try:
                await self._browser.close()
            except:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
        self._initialized = False
        log.info("[transcript-service] Shutdown complete")

    async def _refresh_browser(self, max_retries: int = 6, initial_wait: float = 5.0) -> None:
        """
        Refresh browser connection to prevent stale CDP connections.
        Called automatically after browser_refresh_interval videos.

        If Playwright server crashed and restarted, this will wait and retry
        connecting with exponential backoff (up to ~60s total wait).

        NOTE: Caller must hold _refresh_lock (asyncio locks are not reentrant)
        """
        # Lock is held by caller (_ensure_healthy_browser) - don't acquire again
        log.info(f"[transcript-service] Refreshing browser (after {self._videos_since_refresh} videos)...")
        # 1. Drain and close all pooled contexts
        closed = 0
        while not self._context_pool.empty():
            try:
                ctx = self._context_pool.get_nowait()
                await ctx.close()
                closed += 1
            except:
                pass
        # 2. Close old browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                log.warning(f"[transcript-service] Error closing old browser: {e}")

        # 3. Re-resolve CDP URL and connect with retry (handles Playwright restarts)
        # IMPORTANT: connect_over_cdp can hang indefinitely (known Playwright bug)
        # Must wrap with asyncio.wait_for() to enforce timeout
        cdp_endpoint = self._cdp_endpoint or CDP_HEADED
        connect_timeout = 30.0  # 30 second timeout per attempt
        last_error = None
        for attempt in range(max_retries):
            try:
                # Re-resolve URL each attempt (Playwright may have restarted with new URL)
                log.info(f"[transcript-service] CDP reconnect attempt {attempt + 1}/{max_retries}...")
                self._cdp_url = await asyncio.wait_for(
                    asyncio.to_thread(_get_cdp_websocket_url, cdp_endpoint),
                    timeout=connect_timeout
                )
                # connect_over_cdp can hang forever - enforce timeout
                self._browser = await asyncio.wait_for(
                    self._playwright.chromium.connect_over_cdp(self._cdp_url),
                    timeout=connect_timeout
                )
                log.info(f"[transcript-service] CDP connected (attempt {attempt + 1})")
                break
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"CDP connect timed out after {connect_timeout}s")
                if attempt < max_retries - 1:
                    wait_time = initial_wait * (2 ** attempt)  # 5s, 10s, 20s, 40s...
                    log.warning(f"[transcript-service] CDP connect TIMEOUT (attempt {attempt + 1}/{max_retries}), "
                               f"retrying in {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    log.error(f"[transcript-service] CDP connect timed out after {max_retries} attempts")
                    raise RuntimeError(f"Failed to connect to Playwright CDP after {max_retries} attempts (timeout)") from last_error
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = initial_wait * (2 ** attempt)  # 5s, 10s, 20s, 40s...
                    log.warning(f"[transcript-service] CDP connect failed (attempt {attempt + 1}/{max_retries}), "
                               f"retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    log.error(f"[transcript-service] CDP connect failed after {max_retries} attempts: {e}")
                    raise RuntimeError(f"Failed to connect to Playwright CDP after {max_retries} attempts") from last_error

        # 4. Re-warm context pool
        self._context_pool = asyncio.Queue(maxsize=self.context_pool_size)
        for i in range(self.context_pool_size):
            ctx = await self._create_context()
            await self._context_pool.put(ctx)
        self._videos_since_refresh = 0
        log.info(f"[transcript-service] Browser refreshed (closed {closed} contexts, warmed {self.context_pool_size} new)")

    async def _cleanup_contexts(self) -> None:
        """
        Force close all pooled contexts and recreate fresh ones.
        Called after each batch to prevent memory accumulation.
        """
        async with self._refresh_lock:
            # Drain and close all contexts
            closed = 0
            while not self._context_pool.empty():
                try:
                    ctx = self._context_pool.get_nowait()
                    await ctx.close()
                    closed += 1
                except:
                    pass
            # Recreate fresh contexts
            for i in range(self.context_pool_size):
                try:
                    ctx = await self._create_context()
                    await self._context_pool.put(ctx)
                except Exception as e:
                    log.warning(f"[transcript-service] Failed to recreate context {i}: {e}")
            log.info(f"[transcript-service] Cleanup: closed {closed}, recreated {self._context_pool.qsize()} contexts")

    async def _check_browser_health(self) -> bool:
        """
        Check if browser connection is still healthy.
        Returns True if healthy, False if refresh needed.
        """
        if not self._browser:
            return False
        try:
            # Quick health check - check if browser is connected
            if not self._browser.is_connected():
                return False
            return True
        except Exception as e:
            log.warning(f"[transcript-service] Browser health check failed: {e}")
            return False

    async def _ensure_healthy_browser(self) -> None:
        """Ensure browser is healthy, refresh if needed."""
        # Only refresh on actual browser health failure (not periodic)
        if not await self._check_browser_health():
            # Wait for active operations to complete before refresh
            async with self._refresh_lock:
                # Double-check after acquiring lock (another thread might have refreshed)
                if await self._check_browser_health():
                    return
                # Wait for active ops to drain (max 30s)
                for _ in range(60):
                    async with self._active_ops_lock:
                        if self._active_ops == 0:
                            break
                    await asyncio.sleep(0.5)
                log.warning(f"[transcript-service] Browser unhealthy, refreshing (active_ops={self._active_ops})")
                await self._refresh_browser()

    async def _create_context(self):
        """Create a new browser context with optimized settings."""
        return await self._browser.new_context(
            viewport = {"width": 1920, "height": 1080},  # Full HD to avoid mobile UI
        )

    async def _acquire_context(self, timeout: float = 30.0):
        """Get a context from pool, waiting if necessary."""
        try:
            # Wait for context with timeout instead of creating unlimited temps
            return await asyncio.wait_for(
                self._context_pool.get(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            log.warning("[transcript-service] Context pool timeout, creating temporary")
            return await self._create_context()

    async def _release_context(self, ctx, reuse: bool = True, timeout: float = 5.0) -> None:
        """Return context to pool or close it (with timeout to prevent hangs)."""
        async def _close_ctx():
            try:
                await ctx.close()
            except:
                pass

        if not reuse:
            try:
                await asyncio.wait_for(_close_ctx(), timeout=timeout)
            except asyncio.TimeoutError:
                log.warning("[transcript-service] Context close timed out")
            return

        if self._context_pool.qsize() < self.context_pool_size:
            try:
                # Clear cookies for clean reuse (with timeout)
                await asyncio.wait_for(ctx.clear_cookies(), timeout=timeout)
                self._context_pool.put_nowait(ctx)
            except asyncio.TimeoutError:
                log.warning("[transcript-service] Cookie clear timed out, discarding context")
            except:
                try:
                    await asyncio.wait_for(_close_ctx(), timeout=timeout)
                except:
                    pass
        else:
            try:
                await asyncio.wait_for(_close_ctx(), timeout=timeout)
            except:
                pass

    async def fetch_single(
        self,
        video_id: str,
        prefer_manual: bool = True,
    ) -> dict:
        """
        Fetch transcript for a single video with semaphore control and retry.

        Args:
            video_id: YouTube video ID
            prefer_manual: Prefer manual transcripts over auto-generated

        Returns:
            dict with video_id, page_content, language, segments, etc.
        """
        # Try with retries using exponential backoff
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await self._fetch_single_attempt(video_id, prefer_manual, attempt)
                if "error" not in result:
                    return result
                # If it's a content error (no transcript), don't retry
                error_msg = result.get("error", "").lower()
                if any(x in error_msg for x in ["no transcript", "button not found", "unavailable"]):
                    return result
                last_error = result.get("error")
            except Exception as e:
                last_error = str(e)
                # Connection errors are handled by _fetch_single_attempt
                # Don't refresh here - let the health check in _fetch_single_attempt handle it
            if attempt < self.max_retries:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                log.info(f"[transcript-service] {video_id} retry {attempt + 1}/{self.max_retries} in {wait_time}s")
                await asyncio.sleep(wait_time)
        return {"video_id": video_id, "error": last_error or "Max retries exceeded"}

    async def _fetch_single_attempt(
        self,
        video_id: str,
        prefer_manual: bool,
        attempt: int = 0,
    ) -> dict:
        """Single extraction attempt (called by fetch_batch with batch retry)."""
        start_time = time.time()

        # Small staggered delay to reduce CDP pressure (0-500ms based on attempt)
        if attempt == 0:
            await asyncio.sleep(0.1 * (hash(video_id) % 5))

        async with self.semaphore:
            # Check browser health FIRST (before incrementing active_ops)
            # This prevents race condition where new ops increment while refresh waits
            await self._ensure_healthy_browser()

            # NOW track active operations (after browser is confirmed healthy)
            async with self._active_ops_lock:
                self._active_ops += 1

            context = await self._acquire_context()
            page = None
            reuse_context = True
            try:
                self._videos_since_refresh += 1
                self._total_extractions += 1
                page = await context.new_page()
                await _setup_routes(page)
                url = f"https://www.youtube.com/watch?v={video_id}"
                # Navigate and wait for full page load
                await page.goto(
                    url,
                    wait_until = "load",
                    timeout = self.navigation_timeout_ms)
                await _kill_youtube_background(page)
                # Wait for captions data
                try:
                    await page.wait_for_function(
                        '() => !!window.ytInitialPlayerResponse?.captions',
                        timeout=5000
                    )
                except:
                    pass
                # Get caption tracks
                tracks = await _get_caption_tracks(page)
                language = "auto"
                is_auto_generated = True
                if tracks:
                    manual_count = sum(1 for t in tracks if not t.is_auto_generated)
                    log.info(f"[transcript-service] {video_id}: tracks={len(tracks)} manual={manual_count}")
                    selected = _select_best_track(tracks, prefer_manual)
                    language = selected.language_code
                    is_auto_generated = selected.is_auto_generated
                # DOM scraping (direct API removed - always fails without PO token)
                raw_text = await _extract_via_dom(page, self.timeout_ms)
                if not raw_text:
                    raise ValueError(f"No transcript for: {video_id}")
                segments = _parse_transcript(raw_text)
                page_content = " ".join([seg.text for seg in segments])
                if "auto-generated" in raw_text.lower():
                    is_auto_generated = True
                elapsed = time.time() - start_time
                log.info(f"[transcript-service] OK {video_id} method=dom_scrape segments={len(segments)} time={elapsed:.2f}s")
                return {
                    "video_id": video_id,
                    "language": language,
                    "is_auto_generated": is_auto_generated,
                    "page_content": page_content,
                    "segments": [{"timestamp": s.timestamp, "text": s.text} for s in segments],
                    "method": "dom_scrape",
                }
            except Exception as e:
                reuse_context = False  # Don't reuse context on error
                self._total_errors += 1
                elapsed = time.time() - start_time
                error_str = str(e)
                log.error(f"[transcript-service] FAIL {video_id} time={elapsed:.2f}s error={error_str[:100]}")
                return {
                    "video_id": video_id,
                    "error": error_str,
                }
            finally:
                # Decrement active ops FIRST (before any potentially hanging operations)
                async with self._active_ops_lock:
                    self._active_ops -= 1
                # Then cleanup (may hang if browser died, but won't block refresh)
                if page:
                    try:
                        await page.close()
                    except:
                        pass
                await self._release_context(context, reuse=reuse_context)

    async def fetch_batch(
        self,
        video_ids: list[str],
        prefer_manual: bool = True,
    ) -> list[dict]:
        """
        Fetch transcripts for multiple videos with batch retry strategy.

        Strategy:
        1. First pass: Try all videos once (no inline retry)
        2. Classify failures as retryable or permanent
        3. Retry passes: Retry all retryable failures together after cooldown

        Retryable errors: panel not loaded, timeout, target closed, navigation
        Permanent errors: button not found, no transcript, unavailable

        Args:
            video_ids: List of YouTube video IDs
            prefer_manual: Prefer manual transcripts over auto-generated

        Returns:
            List of transcript dicts (same order as video_ids)
        """
        if not self._initialized:
            raise RuntimeError("PlaywrightTranscriptService not initialized. Call initialize() first.")

        batch_size = len(video_ids)
        log.info(f"[transcript-service] Batch started: {batch_size} videos "
                 f"(max_concurrent={self.max_concurrent}, batch_retries={self.max_retries})")
        start_time = time.time()

        # Results dict to track all outcomes (preserves order)
        results_map: dict[str, dict] = {}

        # Error classification
        # Only truly permanent errors (video confirmed to have no transcript)
        PERMANENT_ERRORS = ["no transcript", "unavailable", "video unavailable", "private video"]
        # Retryable errors (timing issues, connection problems, page load issues)
        RETRYABLE_ERRORS = ["button not found", "panel not loaded", "timeout", "target closed", "navigation", "browser", "context", "expand"]

        def is_retryable(error_msg: str) -> bool:
            error_lower = error_msg.lower()
            if any(p in error_lower for p in PERMANENT_ERRORS):
                return False
            return any(r in error_lower for r in RETRYABLE_ERRORS)

        # Track pending videos for retry passes
        pending_ids = list(video_ids)

        for pass_num in range(self.max_retries + 1):
            if not pending_ids:
                break

            pass_label = "First pass" if pass_num == 0 else f"Retry pass {pass_num}"
            log.info(f"[transcript-service] {pass_label}: {len(pending_ids)} videos")

            # Run all pending videos concurrently (semaphore limits to max_concurrent)
            tasks = [self._fetch_single_attempt(vid, prefer_manual, pass_num) for vid in pending_ids]
            pass_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results and collect retryable failures
            next_pending = []
            for vid, result in zip(pending_ids, pass_results):
                if isinstance(result, Exception):
                    error_str = str(result)
                    result = {"video_id": vid, "error": error_str}

                if "error" not in result:
                    # Success
                    results_map[vid] = result
                else:
                    error_msg = result.get("error", "")
                    if is_retryable(error_msg) and pass_num < self.max_retries:
                        next_pending.append(vid)
                    else:
                        # Permanent failure or max retries reached
                        results_map[vid] = result

            pending_ids = next_pending

            # Cooldown before retry (let YouTube/CDP stabilize)
            if pending_ids and pass_num < self.max_retries:
                cooldown = 3 + pass_num * 2  # 3s, 5s, 7s...
                log.info(f"[transcript-service] {len(pending_ids)} retryable failures, waiting {cooldown}s before retry")
                await asyncio.sleep(cooldown)
                # Don't force refresh here - let health check handle it naturally
                # This prevents closing contexts while other ops might be in-flight

        # Build final results in original order
        results = [results_map.get(vid, {"video_id": vid, "error": "Not processed"}) for vid in video_ids]

        elapsed = time.time() - start_time
        success = sum(1 for r in results if "error" not in r)
        avg_time = elapsed / batch_size if batch_size > 0 else 0
        log.info(f"[transcript-service] Batch complete: {success}/{batch_size} OK "
                 f"time={elapsed:.1f}s avg={avg_time:.1f}s/video")

        # Cleanup contexts after batch to prevent memory accumulation
        await self._cleanup_contexts()

        return results


# Global service instance (initialized at FastAPI lifespan)
_transcript_service: PlaywrightTranscriptService | None = None


def get_transcript_service() -> PlaywrightTranscriptService:
    """Get the global transcript service instance."""
    global _transcript_service
    if _transcript_service is None:
        _transcript_service = PlaywrightTranscriptService()
    return _transcript_service


async def init_transcript_service(
    max_concurrent: int = 5,
    context_pool_size: int | None = None,
    navigation_timeout_ms: int = 60000,
    browser_refresh_interval: int = 15,
    max_retries: int = 2,
) -> PlaywrightTranscriptService:
    """
    Initialize the global transcript service. Call at FastAPI lifespan startup.

    Args:
        max_concurrent: Maximum parallel transcript extractions
        context_pool_size: Pool size (defaults to max_concurrent to avoid context creation storms)
        navigation_timeout_ms: Timeout for Page.goto (default: 60s)
        browser_refresh_interval: Recreate browser every N videos (prevents stale CDP)
        max_retries: Max retries per video with exponential backoff
    """
    global _transcript_service
    _transcript_service = PlaywrightTranscriptService(
        max_concurrent = max_concurrent,
        context_pool_size = context_pool_size,  # Will default to max_concurrent if None
        navigation_timeout_ms = navigation_timeout_ms,
        browser_refresh_interval = browser_refresh_interval,
        max_retries = max_retries,
    )
    await _transcript_service.initialize()
    return _transcript_service


async def close_transcript_service() -> None:
    """Close the global transcript service. Call at FastAPI lifespan shutdown."""
    global _transcript_service
    if _transcript_service:
        await _transcript_service.close()
        _transcript_service = None


# =============================================================================
# ElasticSearch Indexing
# =============================================================================
async def index_videos_to_elasticsearch(
    es_client,
    videos: list[dict],
    index: str = ES_INDEX_METADATA,
) -> dict:
    """
    Index video metadata to ElasticSearch in bulk.
    Returns summary of indexed/failed documents.
    """
    if not videos:
        log.info("[elasticsearch] skip indexing, no videos")
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
        log.info("[elasticsearch] skip indexing, no valid video IDs")
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


async def index_transcriptions_to_elasticsearch(
    es_client,
    transcriptions: list[dict],
    index: str = ES_INDEX_TRANSCRIPTIONS,
) -> dict:
    """
    Index transcriptions to ElasticSearch in bulk.
    Each transcription document has: id, video_id, lang, content, is_auto, method, _extracted_at
    Returns summary of indexed/failed documents.
    """
    if not transcriptions:
        log.info("[elasticsearch] skip indexing, no transcriptions")
        return {"indexed": 0, "failed": 0}
    # Build bulk operations
    operations = []
    for trans in transcriptions:
        doc_id = trans.get("id")  # Composite ID: {video_id}_{lang}
        if not doc_id:
            continue
        operations.append({"index": {"_index": index, "_id": doc_id}})
        operations.append(trans)
    if not operations:
        log.info("[elasticsearch] skip indexing, no valid transcription IDs")
        return {"indexed": 0, "failed": 0}
    # Execute bulk
    log.info(f"[elasticsearch] indexing {len(operations)//2} transcriptions to {index}")
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
        return {"indexed": 0, "failed": len(transcriptions), "error": str(e)}


async def create_youtube_indexes(es_client) -> dict:
    """
    Create both ElasticSearch indexes for YouTube data:
    - coelhonexus-youtube-metadata: Video metadata (title, channel, views, etc.)
    - coelhonexus-youtube-transcriptions: Transcriptions (one doc per video+language)
    """
    results = {}
    # Metadata index mapping
    metadata_mapping = {
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
                # Subtitles (available languages from yt-dlp)
                "subtitles": {"type": "keyword"},
                "automatic_captions": {"type": "keyword"},
                # Extraction metadata
                "_extracted_at": {"type": "date"},
            }
        },
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        }
    }
    # Transcriptions index mapping
    transcriptions_mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},           # Composite: {video_id}_{lang}
                "video_id": {"type": "keyword"},     # YouTube video ID
                "lang": {"type": "keyword"},         # Language code (en, pt, es, etc.)
                "content": {"type": "text", "analyzer": "standard"},  # Full transcription text
                "is_auto": {"type": "boolean"},      # True if auto-generated
                "method": {"type": "keyword"},       # Extraction method (dom_scrape, direct_api)
                "_extracted_at": {"type": "date"},   # When transcription was extracted
            }
        },
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        }
    }
    # Create metadata index
    try:
        exists = await es_client.indices.exists(index = ES_INDEX_METADATA)
        if not exists:
            await es_client.indices.create(
                index = ES_INDEX_METADATA,
                mappings = metadata_mapping["mappings"],
                settings = metadata_mapping["settings"],
            )
            results["metadata"] = {"created": True, "index": ES_INDEX_METADATA}
        else:
            results["metadata"] = {"created": False, "index": ES_INDEX_METADATA, "message": "exists"}
    except Exception as e:
        results["metadata"] = {"created": False, "index": ES_INDEX_METADATA, "error": str(e)}
    # Create transcriptions index
    try:
        exists = await es_client.indices.exists(index = ES_INDEX_TRANSCRIPTIONS)
        if not exists:
            await es_client.indices.create(
                index = ES_INDEX_TRANSCRIPTIONS,
                mappings = transcriptions_mapping["mappings"],
                settings = transcriptions_mapping["settings"],
            )
            results["transcriptions"] = {"created": True, "index": ES_INDEX_TRANSCRIPTIONS}
        else:
            results["transcriptions"] = {"created": False, "index": ES_INDEX_TRANSCRIPTIONS, "message": "exists"}
    except Exception as e:
        results["transcriptions"] = {"created": False, "index": ES_INDEX_TRANSCRIPTIONS, "error": str(e)}
    return results
