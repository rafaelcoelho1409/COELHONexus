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

        # Python-side filter: show-only-one-kind. Each snippet's `kind`
        # was set by `detect_entry_kind` during normalization, which is
        # more reliable than yt-dlp's sparse `_type` in --flat-playlist
        # mode. ytsearch: is video-focused so non-video kind_filters
        # often yield 0 results — intentional, see schemas.py.
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


_service: YtDlpSearchService | None = None


def get_search_service() -> YtDlpSearchService:
    """Singleton accessor — mirror of `domains.dd.ingestion.storage.get_storage`."""
    global _service
    if _service is None:
        _service = YtDlpSearchService()
    return _service
