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
    MAX_CONCURRENT,
    SEARCH_TIMEOUT_S,
    TIMEOUT_S,
)
from .schemas import SearchRequest, SearchResponse, VideoSnippet


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
