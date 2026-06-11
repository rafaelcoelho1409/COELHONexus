"""I/O orchestration for the Semantic Scholar tool — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §4: async + httpx + logging here; all parsing
delegated to domain.parse_search_response (pure); rate-limiting handled by
the cross-cutting RateLimitMiddleware (declared in tool.py).

Reads top-to-bottom as the algorithm:
  build params → build headers (+ optional API key) → GET → parse → return.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx

from .config import S2
from .domain import parse_search_response
from .keys import DEFAULT_FIELDS
from .schemas import Paper, SearchInput

if TYPE_CHECKING:
    from fastmcp import Context


logger = logging.getLogger(__name__)


def _build_params(req: SearchInput) -> dict[str, str | int]:
    """Compose the /paper/search querystring. Every filter is server-side."""
    params: dict[str, str | int] = {
        "query": req.query,
        "limit": min(req.n_max, S2.max_results_per_call),
        # S2's `fields` parameter is the single biggest result-quality lever.
        # Without it, the response is barren (no tldr, no citations).
        "fields": ",".join(DEFAULT_FIELDS),
    }

    year_filter = _build_year_filter(req)
    if year_filter:
        params["year"] = year_filter
    if req.fields_of_study:
        params["fieldsOfStudy"] = ",".join(req.fields_of_study)
    if req.min_citation_count is not None:
        params["minCitationCount"] = req.min_citation_count
    if req.venue_filter:
        params["venue"] = ",".join(req.venue_filter)

    return params


def _build_year_filter(req: SearchInput) -> str | None:
    """S2 expects `year=<min>-<max>` (either side optional)."""
    if req.year_min is None and req.year_max is None:
        return None
    lo = str(req.year_min) if req.year_min is not None else ""
    hi = str(req.year_max) if req.year_max is not None else ""
    return f"{lo}-{hi}"


def _build_headers() -> dict[str, str]:
    """Identity header + optional API key (free; bumps 100/5min → 1 RPS)."""
    headers = {"User-Agent": S2.user_agent}
    api_key = os.environ.get(S2.api_key_env)
    if api_key:
        headers["x-api-key"] = api_key
    return headers


async def search_s2(
    req: SearchInput, ctx: Context | None = None,
) -> list[Paper]:
    """Search S2's /paper/search. Cross-cutting RateLimitMiddleware has
    already blocked us if we're inside the min-interval window."""
    if ctx:
        await ctx.info(f"s2: searching {req.query!r} (n_max={req.n_max})")
        await ctx.report_progress(0.0, 1.0)

    params = _build_params(req)
    headers = _build_headers()

    if ctx:
        await ctx.report_progress(0.25, 1.0)

    async with httpx.AsyncClient(timeout=S2.timeout_s) as client:
        resp = await client.get(
            f"{S2.base_url}/paper/search",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()

    if ctx:
        await ctx.info(f"s2: HTTP {resp.status_code}, {len(resp.content)} bytes")
        await ctx.report_progress(0.6, 1.0)

    body = resp.json()
    total_available = int(body.get("total") or 0)
    papers = parse_search_response(body)

    msg = (
        f"s2: parsed {len(papers)} papers "
        f"(total available: {total_available})"
    )
    if ctx:
        await ctx.info(msg)
        await ctx.report_progress(1.0, 1.0)
    else:
        logger.info(
            "s2.search query=%r returned %d (total %d)",
            req.query, len(papers), total_available,
        )

    return papers
