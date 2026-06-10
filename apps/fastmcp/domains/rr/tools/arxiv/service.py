"""I/O orchestration for the arXiv tool — the Imperative Shell.

Per docs/CODE-CONVENTIONS.md §4: async + httpx + logging here; all parsing
delegated to domain.parse_atom_feed (pure); rate-limiting is now handled by
the cross-cutting RateLimitMiddleware (apps/fastmcp/middleware/ratelimit.py)
which tools.arxiv.tool.register() declares on import.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from .config import ARXIV
from .domain import build_search_query, parse_atom_feed
from .schemas import Paper, SearchInput

if TYPE_CHECKING:
    from fastmcp import Context


logger = logging.getLogger(__name__)


async def search_arxiv(req: SearchInput, ctx: Context | None = None) -> list[Paper]:
    """Search arXiv. The cross-cutting RateLimitMiddleware blocks the call
    BEFORE this function runs (per `ARXIV.min_request_interval_s` registered
    in tools.arxiv.tool); this body just does the HTTP + parse."""
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

    async with httpx.AsyncClient(timeout=ARXIV.timeout_s) as client:
        resp = await client.get(ARXIV.base_url, params=params, headers=headers)
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
