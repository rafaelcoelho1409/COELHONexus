"""ycs/extract — async I/O orchestration for the 4 yt-dlp metadata paths.

The "Imperative Shell" (per `docs/CODE-CONVENTIONS.md` §4) for the
deprecated `YtDlpExtractor`. Pure logic — argv building, projection,
URL/ID parsing — lives in `domain.py`. Subprocess + semaphore + logging
+ error translation live here.

Public surface (mirrors deprecated `helpers.py:L122-439`):
  extract_video(video_id)      → VideoMetadata
  extract_batch(video_ids)     → list[VideoMetadata]    (parallel)
  extract_playlist(playlist_id, max_videos) → PlaylistResult
  extract_channel(channel_id_or_handle, max_videos) → ChannelResult

NO PERSISTENCE in this layer — Celery tasks (Wave 4) wrap these calls +
write to Elasticsearch + dispatch the Playwright transcript fetch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from domains.ycs.content.errors import (
    YtDlpJsonParseError,
    YtDlpSubprocessError,
    YtDlpTimeoutError,
)

from . import domain
from .params import (
    BUFFER_LIMIT_BYTES,
    MAX_CONCURRENT_VIDEOS,
    TIMEOUT_PER_VIDEO_S,
)
from .schemas import (
    ChannelResult,
    PlaylistResult,
    VideoMetadata,
)


logger = logging.getLogger(__name__)


class YtDlpExtractor:
    """Process-bound singleton; the asyncio.Semaphore caps outbound
    subprocesses across concurrent requests. Holding the instance
    long-lived (vs per-request) is what makes the cap effective.

    Mirror of deprecated `YtDlpExtractor` (`helpers.py:L53-546`)."""

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT_VIDEOS,
        default_timeout_s: float = TIMEOUT_PER_VIDEO_S,
        buffer_limit: int = BUFFER_LIMIT_BYTES,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._default_timeout = default_timeout_s
        self._buffer_limit = buffer_limit

    # -------- private subprocess primitive -----------------------------

    async def _run(
        self, args: list[str], timeout_s: float | None = None,
    ) -> str:
        """Spawn `yt-dlp ...`, return stdout. Raises YtDlpTimeoutError /
        YtDlpSubprocessError on failure."""
        effective = timeout_s if timeout_s is not None else self._default_timeout
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.PIPE,
            limit = self._buffer_limit,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout = effective,
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
            elapsed = time.monotonic() - started
            logger.info(f"[yt-dlp] TIMEOUT {elapsed:.2f}s limit={effective}s")
            raise YtDlpTimeoutError(f"yt-dlp exceeded {effective}s") from None

        elapsed = time.monotonic() - started
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors = "replace")
            logger.info(
                f"[yt-dlp] FAIL {elapsed:.2f}s rc={proc.returncode} "
                f"stderr={err[:200]!r}"
            )
            raise YtDlpSubprocessError(err, proc.returncode or -1)

        logger.info(f"[yt-dlp] OK {elapsed:.2f}s out={len(stdout)} bytes")
        return stdout.decode("utf-8", errors = "replace")

    # -------- public extractor methods (mirror deprecated 4-tuple) ----

    async def extract_video(self, video_id: str) -> VideoMetadata:
        """Single video, full --dump-json projection."""
        normalized_id = domain.normalize_video_id(video_id)
        async with self._semaphore:
            stdout = await self._run(domain.build_video_args(normalized_id))
        raw = self._parse_json(stdout)
        projection = domain.normalize_full_video(raw)
        return VideoMetadata.model_validate(projection)

    async def extract_batch(self, video_ids: list[str]) -> list[VideoMetadata]:
        """Parallel extraction. Per-task failures land as `None` in
        gather's result list — caller filters."""
        valid, rejected = domain.normalize_video_ids(video_ids)
        if rejected:
            logger.info(f"[yt-dlp:batch] rejected {len(rejected)} unparseable inputs")
        coros = [self._safe_extract(vid) for vid in valid]
        results = await asyncio.gather(*coros)
        return [v for v in results if v is not None]

    async def _safe_extract(self, video_id: str) -> VideoMetadata | None:
        """Per-batch wrapper: log + return None on per-video failure
        rather than tear down the whole gather."""
        try:
            return await self.extract_video(video_id)
        except (YtDlpTimeoutError, YtDlpSubprocessError, YtDlpJsonParseError) as e:
            logger.info(f"[yt-dlp:batch] skip {video_id}: {type(e).__name__}")
            return None

    async def extract_playlist(
        self, playlist_id: str, max_videos: int = 0,
    ) -> PlaylistResult:
        """Full playlist metadata + per-video projections."""
        normalized_id = domain.normalize_playlist_id(playlist_id)
        args = domain.build_playlist_args(normalized_id, max_videos)
        timeout = domain.aggregate_timeout_s(max_videos)
        async with self._semaphore:
            stdout = await self._run(args, timeout_s = timeout)
        raw = self._parse_json(stdout)
        entries = raw.get("entries") or []
        videos = [
            VideoMetadata.model_validate(domain.normalize_full_video(e or {}))
            for e in entries
            if (e or {}).get("id")
        ]
        return PlaylistResult(
            playlist_id =          raw.get("id"),
            playlist_title =       raw.get("title"),
            playlist_url =         f"https://www.youtube.com/playlist?list={normalized_id}",
            playlist_description = raw.get("description"),
            playlist_uploader =    raw.get("uploader"),
            playlist_uploader_id = raw.get("uploader_id"),
            playlist_count =       raw.get("playlist_count"),
            total_videos =         len(videos),
            videos =               videos,
        )

    async def extract_channel(
        self, channel_id_or_handle: str, max_videos: int = 0,
    ) -> ChannelResult:
        """Full channel metadata + per-video projections."""
        normalized = domain.normalize_channel_id(channel_id_or_handle)
        args = domain.build_channel_args(normalized, max_videos)
        timeout = domain.aggregate_timeout_s(max_videos)
        async with self._semaphore:
            stdout = await self._run(args, timeout_s = timeout)
        raw = self._parse_json(stdout)
        entries = raw.get("entries") or []
        videos = [
            VideoMetadata.model_validate(domain.normalize_full_video(e or {}))
            for e in entries
            if (e or {}).get("id")
        ]
        url = (
            f"https://www.youtube.com/channel/{normalized}/videos"
            if normalized.startswith("UC")
            else f"https://www.youtube.com/{normalized}/videos"
        )
        return ChannelResult(
            channel_id =       raw.get("id") or normalized,
            channel_title =    raw.get("title"),
            channel_url =      url,
            channel_uploader = raw.get("uploader"),
            total_videos =     len(videos),
            videos =           videos,
        )

    @staticmethod
    def _parse_json(stdout: str) -> dict:
        """Translate JSON errors into our domain error type for caller
        translation to HTTPException."""
        try:
            return json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as e:
            raise YtDlpJsonParseError(str(e)) from e


_extractor: YtDlpExtractor | None = None


def get_extractor() -> YtDlpExtractor:
    """Singleton accessor — mirror of deprecated `helpers.py:get_extractor`."""
    global _extractor
    if _extractor is None:
        _extractor = YtDlpExtractor()
    return _extractor
