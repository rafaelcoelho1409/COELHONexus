"""ycs/content — async I/O orchestration for the yt-dlp subprocess search.

The "Imperative Shell" for the content-search subsystem. Reads top-to-
bottom; every pure step delegates to `domain.*`; every side effect
(subprocess, log, clock) is named explicitly here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from . import domain
from .errors import (
    YtDlpJsonParseError,
    YtDlpSubprocessError,
    YtDlpTimeoutError,
)
from .params import (
    BUFFER_LIMIT_BYTES,
    ENUMERATE_ALL_TIMEOUT_S,
    MAX_CONCURRENT,
    SEARCH_TIMEOUT_S,
    TIMEOUT_S,
)
from .schemas import (
    EnumerationResponse,
    SearchRequest,
    SearchResponse,
    VideoSnippet,
)


logger = logging.getLogger(__name__)


class YtDlpSearchService:
    """Process-bound singleton — single asyncio.Semaphore caps outbound
    yt-dlp invocations across all concurrent requests. Holding the
    instance long-lived (vs per-request) lets the semaphore actually
    throttle.

    Constructed lazily via `get_search_service()` so the module imports
    cleanly even without yt-dlp installed (Celery worker uses it; the
    test environment doesn't always)."""

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT,
        default_timeout_s: float = TIMEOUT_S,
        buffer_limit: int = BUFFER_LIMIT_BYTES,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._default_timeout = default_timeout_s
        self._buffer_limit = buffer_limit

    async def _run(
        self, args: list[str], timeout_s: float | None = None,
    ) -> str:
        """Spawn `yt-dlp ...`, return stdout (UTF-8 decoded). Raises
        YtDlpTimeoutError / YtDlpSubprocessError on failure."""
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

    async def search(self, req: SearchRequest) -> SearchResponse:
        """End-to-end: build args (pure) → run subprocess → normalize entries
        (pure) → cap at `req.max_results` → return envelope."""
        conditions = domain.build_match_conditions(req)
        fetch_count = domain.effective_fetch_count(req.max_results, conditions)
        args = domain.build_search_args(
            query = req.query,
            fetch_count = fetch_count,
            sort_by_date = req.sort_by_date,
            conditions = conditions,
            date_after = req.date_after,
            date_before = req.date_before,
            age_limit = req.age_limit,
            # Routes channel/playlist filters to the search-URL extractor
            # (`youtube.com/results?...&sp=<type>`). `ytsearch:` is
            # video-only, so a channel-name query like "Raiam Santos
            # McArn" would otherwise return 0 channel hits.
            kind_filter = req.kind_filter,
        )

        logger.info(
            f"[ycs:search] q={req.query!r} max={req.max_results} "
            f"fetch={fetch_count} filters={len(conditions)} "
            f"sort_date={req.sort_by_date}"
        )
        started = time.monotonic()
        async with self._semaphore:
            stdout = await self._run(args, timeout_s = SEARCH_TIMEOUT_S)
        elapsed = time.monotonic() - started

        try:
            payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as e:
            logger.info(f"[ycs:search] JSON_ERROR q={req.query!r}: {e}")
            raise YtDlpJsonParseError(str(e)) from e

        snippets: list[VideoSnippet] = []
        for entry in payload.get("entries", []) or []:
            normalized = domain.normalize_search_entry(entry)
            if not normalized:
                continue
            snippets.append(VideoSnippet.model_validate(normalized))
            if len(snippets) >= req.max_results:
                break

        # Python-side filter belt-and-suspenders. The PRIMARY filter is
        # now upstream in `build_search_args` — channel/playlist queries
        # route to the search-URL extractor with `sp=` Type filter, so
        # the response is mostly homogeneous. This pass still strips any
        # straggler whose classifier-derived `kind` disagrees (rare:
        # yt-dlp's sparse `_type` in --flat-playlist mode occasionally
        # misclassifies; `detect_entry_kind` is stricter).
        if req.kind_filter:
            snippets = [s for s in snippets if s.kind == req.kind_filter]

        # Python-side sort when the user picked the `Sort: newest` filter.
        # Replaces yt-dlp's `ytsearchdate:` prefix which started returning
        # `Unable to handle request` on yt-dlp 2026.3.17 (see domain.py).
        # Stable sort: entries with `upload_date` ranked newest-first;
        # entries without a date fall to the bottom.
        if req.sort_by_date:
            snippets.sort(
                key = lambda s: s.upload_date or "00000000",
                reverse = True,
            )

        # Channel/playlist video-count fan-out. Each channel/playlist
        # snippet gets its `video_count` filled from a cheap
        # `--playlist-items 1` probe (UU-prefixed uploads playlist for
        # channels, the playlist URL directly for playlists). Runs in
        # parallel — slowest probe gates total latency, not the sum.
        # Best-effort: a probe that times out or errors leaves
        # `video_count` as None and renders as "Channel" / "Playlist"
        # without a count, never as a failure.
        if any(s.kind in ("channel", "playlist") for s in snippets):
            await self._populate_video_counts(snippets)

        logger.info(
            f"[ycs:search] OK q={req.query!r} hits={len(snippets)} "
            f"elapsed={elapsed:.2f}s"
        )
        return SearchResponse(
            query = req.query,
            total = len(snippets),
            results = snippets,
            fetched_for = fetch_count,
            elapsed_s = round(elapsed, 3),
        )


    async def enumerate_videos(
        self,
        source:    str,          # "channel" | "playlist"
        raw_input: str,
        limit:     int  = 100,
        offset:    int  = 0,
    ) -> EnumerationResponse:
        """List videos in ONE channel or playlist with pagination. Used
        by the redesigned Channel + Playlist tabs to render a master+row
        checkbox picker so the user can submit a subset (or all) of the
        videos to the existing `/content/videos/pipeline` chain.

        Pagination uses yt-dlp's `--playlist-items <off+1>:<off+limit>`
        range (1-indexed, inclusive). yt-dlp still walks the playlist
        from index 1 internally on each call, so deep pages on a giant
        channel are linearly costlier than shallow ones — the frontend's
        "Load more" button is what makes this acceptable (each Load more
        is one yt-dlp call returning `limit` more rows). For typical
        channels (~hundreds to ~few thousand videos), this is fine.

        Bound checks: limit clamped to [1, 500], offset to [0, ∞)."""
        if source == "channel":
            target_url = domain.resolve_channel_input(raw_input)
        elif source == "playlist":
            target_url = domain.resolve_playlist_input(raw_input)
        else:
            raise ValueError(f"source must be 'channel' or 'playlist', got {source!r}")
        if not target_url:
            raise ValueError("empty input")
        lo = max(1, int(offset) + 1)
        hi = max(lo, int(offset) + max(1, min(int(limit), 500)))
        args = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--playlist-items", f"{lo}:{hi}",
            target_url,
        ]
        logger.info(
            f"[ycs:enumerate] source={source} target={target_url!r} "
            f"items={lo}-{hi}"
        )
        started = time.monotonic()
        async with self._semaphore:
            stdout = await self._run(args, timeout_s = SEARCH_TIMEOUT_S)
        elapsed = time.monotonic() - started

        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as e:
            logger.info(f"[ycs:enumerate] JSON_ERROR {target_url!r}: {e}")
            raise YtDlpJsonParseError(str(e)) from e

        snippets: list[VideoSnippet] = []
        for entry in data.get("entries", []) or []:
            normalized = domain.normalize_search_entry(entry)
            if not normalized:
                continue
            # In flat-playlist mode every entry inside a channel/playlist
            # is a video — but `detect_entry_kind` may misclassify the
            # uploads-playlist parent. Force `kind="video"` for the
            # enumerated entries.
            normalized["kind"] = "video"
            snippets.append(VideoSnippet.model_validate(normalized))

        total = data.get("playlist_count")
        try:
            total_n = int(total) if total is not None else None
        except (TypeError, ValueError):
            total_n = None

        title = data.get("title")
        # Channels often title themselves "<name> - Videos" or include
        # tab markers — strip those for cleaner display.
        if isinstance(title, str):
            title = title.removesuffix(" - Videos")

        # `channel` field — yt-dlp populates it on playlist queries
        # (the owning channel). For channel enumerations the title
        # already IS the channel, so we mirror it.
        chan = data.get("channel") or (title if source == "channel" else None)

        has_more = (
            total_n is not None and (offset + len(snippets)) < total_n
        ) or (
            # Fallback: if we got exactly `limit` items and total is
            # unknown, assume there are more.
            total_n is None and len(snippets) >= (hi - lo + 1)
        )

        logger.info(
            f"[ycs:enumerate] OK source={source} target={target_url!r} "
            f"page_items={len(snippets)} total={total_n} elapsed={elapsed:.2f}s"
        )
        return EnumerationResponse(
            source    = source,
            source_id = raw_input,
            title     = title,
            channel   = chan,
            total     = total_n,
            offset    = int(offset),
            limit     = max(1, min(int(limit), 500)),
            has_more  = has_more,
            items     = snippets,
        )

    async def enumerate_all_video_ids(
        self,
        source:    str,          # "channel" | "playlist"
        raw_input: str,
    ) -> list[str]:
        """Walk the ENTIRE channel/playlist in one yt-dlp call, returning
        every video ID (in source order, dedup-preserved). Used by the
        `Ingest all` button on the Source · Channel / Playlist tabs so
        a single click queues the whole source regardless of the 100-
        per-page picker cap.

        `--print "%(id)s"` is the cheapest enumeration mode — emits one
        ID per line, no JSON envelope, no per-video metadata. For a
        10k-video channel that's ~120KB of stdout vs ~30MB of full
        --dump-single-json, and avoids the 32MB buffer ceiling on
        truly large sources (Joe Rogan, MrBeast, etc.).

        Timeout is the longer ENUMERATE_ALL_TIMEOUT_S (5 min default)
        since walking a 30k-video channel can take ~2-3 min — that's
        the trade for not making the client paginate."""
        if source == "channel":
            target_url = domain.resolve_channel_input(raw_input)
        elif source == "playlist":
            target_url = domain.resolve_playlist_input(raw_input)
        else:
            raise ValueError(
                f"source must be 'channel' or 'playlist', got {source!r}",
            )
        if not target_url:
            raise ValueError("empty input")
        args = [
            "yt-dlp",
            "--flat-playlist",
            "--no-warnings",
            "--print", "%(id)s",
            target_url,
        ]
        logger.info(
            f"[ycs:enumerate-all] source={source} target={target_url!r}"
        )
        started = time.monotonic()
        async with self._semaphore:
            stdout = await self._run(
                args, timeout_s = ENUMERATE_ALL_TIMEOUT_S,
            )
        elapsed = time.monotonic() - started

        ids: list[str] = []
        seen: set[str] = set()
        for line in stdout.splitlines():
            vid = line.strip()
            if not vid or vid in seen:
                continue
            seen.add(vid)
            ids.append(vid)

        logger.info(
            f"[ycs:enumerate-all] OK source={source} target={target_url!r} "
            f"ids={len(ids)} elapsed={elapsed:.2f}s"
        )
        return ids

    async def preview_videos(
        self,
        video_ids: list[str],
        limit:     int = 100,
        offset:    int = 0,
    ) -> EnumerationResponse:
        """Fetch yt-dlp metadata for a list of video IDs and project
        into the same EnumerationResponse shape Channel/Playlist tabs
        use, so the Source page's Videos tab can render the SAME
        master+row checkbox picker UI.

        Server paginates the input list (slice by offset/limit) so
        wirePickerTab's Load-more keeps working — paste 200 IDs, page
        through 100-at-a-time. Per-video extract failures (private
        videos, deleted, geo-blocked, etc.) silently drop from the
        page; `total` reflects the original input size."""
        from domains.ycs.extract.service import get_extractor

        ids = [vid for vid in (video_ids or []) if vid]
        total = len(ids)
        lo = max(0, int(offset))
        hi = lo + max(1, min(int(limit), 500))
        page_ids = ids[lo:hi]
        if not page_ids:
            return EnumerationResponse(
                source    = "videos",
                source_id = "",
                title     = None,
                channel   = None,
                total     = total,
                offset    = lo,
                limit     = max(1, min(int(limit), 500)),
                has_more  = False,
                items     = [],
            )

        extractor = get_extractor()
        started = time.monotonic()
        videos = await extractor.extract_batch(page_ids)
        elapsed = time.monotonic() - started

        # Project VideoMetadata → VideoSnippet (picker.js consumes the
        # same shape). `extra="allow"` on VideoMetadata means we can
        # access .channel_url / .thumbnail_url verbatim.
        snippets: list[VideoSnippet] = []
        for v in videos:
            d = v.model_dump() if hasattr(v, "model_dump") else dict(v)
            normalized = {
                "id":              d.get("id") or "",
                "kind":            "video",
                "title":           d.get("title"),
                "url":             d.get("webpage_url") or f"https://www.youtube.com/watch?v={d.get('id', '')}",
                "duration":        d.get("duration"),
                "duration_string": d.get("duration_string"),
                "view_count":      d.get("view_count"),
                "like_count":      d.get("like_count"),
                "channel":         d.get("channel"),
                "channel_id":      d.get("channel_id"),
                "channel_url":     d.get("channel_url"),
                "thumbnail":       (
                    domain._absolutize_thumbnail_url(d.get("thumbnail_url") or "")
                    or domain.pick_best_thumbnail(d.get("thumbnails"))
                ),
                "description":     d.get("description"),
                "upload_date":     d.get("upload_date"),
                "timestamp":         d.get("timestamp"),
                "release_timestamp": d.get("release_timestamp"),
                "live_status":     d.get("live_status"),
                "availability":    d.get("availability"),
            }
            if not normalized["id"]:
                continue
            snippets.append(VideoSnippet.model_validate(normalized))

        logger.info(
            f"[ycs:preview] OK ids={total} page_items={len(snippets)} "
            f"slice={lo}:{hi} elapsed={elapsed:.2f}s"
        )
        return EnumerationResponse(
            source    = "videos",
            source_id = "",
            title     = "Pasted videos",
            channel   = None,
            total     = total,
            offset    = lo,
            limit     = max(1, min(int(limit), 500)),
            has_more  = (lo + len(page_ids)) < total,
            items     = snippets,
        )

    async def _probe_video_count(self, probe_url: str) -> int | None:
        """Cheap fetch — `--flat-playlist --playlist-items 1` returns the
        playlist metadata (including `playlist_count`) in ~2s instead of
        the ~20s a full channel enumeration takes. Returns None on any
        failure so the caller renders gracefully without the count."""
        args = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-items", "1",
            "--no-warnings",
            probe_url,
        ]
        try:
            stdout = await self._run(args, timeout_s = 15.0)
        except Exception as e:
            logger.info(f"[ycs:search] count-probe FAIL {probe_url}: {e}")
            return None
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return None
        n = data.get("playlist_count")
        try:
            return int(n) if n is not None else None
        except (TypeError, ValueError):
            return None

    async def _populate_video_counts(
        self, snippets: list[VideoSnippet],
    ) -> None:
        """In-place fill of `video_count` for every channel/playlist
        snippet. Probes run in parallel under the same semaphore that
        gates the main search, so a burst of channel hits in one query
        can't blow the cluster's egress budget."""
        probe_targets: list[tuple[VideoSnippet, str]] = []
        for s in snippets:
            if s.kind not in ("channel", "playlist"):
                continue
            url = domain.count_probe_url(s.model_dump())
            if url:
                probe_targets.append((s, url))
        if not probe_targets:
            return

        async def _one(s: VideoSnippet, url: str) -> None:
            async with self._semaphore:
                n = await self._probe_video_count(url)
            if n is not None:
                # Pydantic v2 — re-validate would reject extras; use
                # __dict__ since `extra="forbid"` blocks setattr through
                # the normal path on some model configs. video_count is
                # declared on the model, so direct attribute set works.
                s.video_count = n

        import asyncio as _asyncio
        await _asyncio.gather(
            *[_one(s, u) for s, u in probe_targets],
            return_exceptions = False,
        )


_service: YtDlpSearchService | None = None


def get_search_service() -> YtDlpSearchService:
    """Singleton accessor — mirror of `domains.dd.ingestion.storage.get_storage`."""
    global _service
    if _service is None:
        _service = YtDlpSearchService()
    return _service
