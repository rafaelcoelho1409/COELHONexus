"""I/O orchestration for the arXiv tool — the Imperative Shell.

Per docs/CODE-CONVENTIONS.md §4: async + httpx + logging here; all parsing
delegated to domain.parse_atom_feed (pure); rate-limiting is now handled by
the cross-cutting RateLimitMiddleware (apps/fastmcp/middleware/ratelimit.py)
which tools.arxiv.tool.register() declares on import.

arXiv answers overload with a bare HTTP 406 (not 429) — confirmed via
multiple 2026 community reports — and it does NOT clear on an immediate
retry, so `_fetch_with_retry` backs off before retrying, then trips the
shared circuit breaker (`ratelimit.trip_breaker`) once retries are
exhausted so the REST of this scan's discovery subagents (and a
re-triggered scan within the cooldown) fail fast instead of each
independently re-discovering the same overload.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from middleware import ratelimit

from .config import ARXIV
from .domain import build_search_query, parse_atom_feed
from .keys import TOOL_NAME
from .schemas import Paper, SearchInput

if TYPE_CHECKING:
    from fastmcp import Context


logger = logging.getLogger(__name__)


async def _fetch_with_retry(params: dict, headers: dict) -> httpx.Response:
    """GET with backoff-then-retry on 406/503 (arXiv's load-shedding
    signals). Trips the circuit breaker once `ARXIV.retry_max_attempts`
    is exhausted; caller still calls `raise_for_status()` on the result,
    so a final failure surfaces as the normal `httpx.HTTPStatusError`."""
    resp: httpx.Response | None = None
    for attempt in range(ARXIV.retry_max_attempts):
        async with httpx.AsyncClient(timeout=ARXIV.timeout_s) as client:
            resp = await client.get(ARXIV.base_url, params=params, headers=headers)
        if resp.status_code not in (406, 503):
            return resp
        if attempt < ARXIV.retry_max_attempts - 1:
            backoff = ARXIV.retry_backoff_base_s * (attempt + 1)
            logger.warning(
                f"arxiv.search got HTTP {resp.status_code} (load-shedding) "
                f"— backing off {backoff:.0f}s before retry "
                f"{attempt + 2}/{ARXIV.retry_max_attempts}"
            )
            await asyncio.sleep(backoff)
    ratelimit.trip_breaker(TOOL_NAME, ARXIV.circuit_breaker_cooldown_s)
    return resp


async def search_arxiv(req: SearchInput, ctx: Context | None = None) -> list[Paper]:
    """Search arXiv. The cross-cutting RateLimitMiddleware blocks the call
    BEFORE this function runs (per `ARXIV.min_request_interval_s` registered
    in tools.arxiv.tool); this body just does the HTTP + parse."""
    breaker_remaining = ratelimit.breaker_tripped(TOOL_NAME)
    if breaker_remaining is not None:
        msg = (
            f"arxiv: circuit breaker open ({breaker_remaining:.0f}s "
            f"remaining) — arXiv signalled overload recently, skipping "
            f"this call rather than re-discovering the same rejection."
        )
        if ctx:
            await ctx.info(msg)
        else:
            logger.warning(msg)
        return []

    if ctx:
        await ctx.info(f"arxiv: searching '{req.query}' (n_max={req.n_max})")
        await ctx.report_progress(0.0, 1.0)

    params = {
        "search_query": build_search_query(req),
        "max_results": min(req.n_max, ARXIV.max_results_per_call),
        "sortBy": req.sort_by,
        "sortOrder": "descending",
    }
    headers = {"User-Agent": ARXIV.user_agent}

    if ctx:
        await ctx.report_progress(0.25, 1.0)

    resp = await _fetch_with_retry(params, headers)
    resp.raise_for_status()

    if ctx:
        await ctx.info(f"arxiv: HTTP {resp.status_code}, {len(resp.content)} bytes")
        await ctx.report_progress(0.6, 1.0)

    papers = parse_atom_feed(resp.text)

    if ctx:
        await ctx.info(f"arxiv: parsed {len(papers)} papers")
        await ctx.report_progress(1.0, 1.0)
    else:
        logger.info("arxiv.search query=%r returned %d papers", req.query, len(papers))

    return papers
