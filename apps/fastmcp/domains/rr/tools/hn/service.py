"""I/O orchestration for the HN tool — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §4: async + httpx + logging here; parsing
delegated to domain.parse_search_response (pure); rate-limiting handled by
the cross-cutting RateLimitMiddleware (declared in tool.py).

Reads top-to-bottom as the algorithm:
  build numericFilters → build params → pick endpoint → GET → parse → return.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import TYPE_CHECKING

import httpx

from .config import HN
from .domain import parse_search_response
from .keys import DEFAULT_TAGS
from .schemas import Hit, SearchInput

if TYPE_CHECKING:
    from fastmcp import Context


logger = logging.getLogger(__name__)


def _build_numeric_filters(req: SearchInput) -> list[str]:
    """Algolia's `numericFilters` are comma-separated `field<op>value` strings.
    Pushed server-side so we don't post-filter (cheaper + preserves ranking)."""
    filters: list[str] = []
    if req.min_points is not None:
        filters.append(f"points>={req.min_points}")
    if req.min_num_comments is not None:
        filters.append(f"num_comments>={req.min_num_comments}")
    if req.since is not None:
        # Algolia uses Unix seconds; combine the date with 00:00 UTC.
        ts = int(datetime.combine(req.since, time.min, tzinfo=timezone.utc).timestamp())
        filters.append(f"created_at_i>={ts}")
    return filters


def _build_params(req: SearchInput) -> dict[str, str | int]:
    """Compose the /search querystring."""
    tags = ",".join(req.tags) if req.tags else ",".join(DEFAULT_TAGS)
    params: dict[str, str | int] = {
        "query": req.query,
        "tags": tags,
        "hitsPerPage": min(req.n_max, HN.max_results_per_call),
    }
    filters = _build_numeric_filters(req)
    if filters:
        params["numericFilters"] = ",".join(filters)
    return params


async def search_hn(req: SearchInput, ctx: Context | None = None) -> list[Hit]:
    """Search HN via Algolia. The cross-cutting RateLimitMiddleware blocks
    before this runs if we're inside the min-interval window."""
    if ctx:
        await ctx.info(f"hn: searching {req.query!r} (n_max={req.n_max}, sort={req.sort_by})")
        await ctx.report_progress(0.0, 1.0)

    params = _build_params(req)
    headers = {"User-Agent": HN.user_agent, "Accept": "application/json"}

    # Algolia exposes two endpoints: relevance vs strict-date order.
    path = "/search" if req.sort_by == "relevance" else "/search_by_date"

    if ctx:
        await ctx.report_progress(0.25, 1.0)

    async with httpx.AsyncClient(timeout=HN.timeout_s) as client:
        resp = await client.get(
            f"{HN.base_url}{path}",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()

    if ctx:
        await ctx.info(f"hn: HTTP {resp.status_code}, {len(resp.content)} bytes")
        await ctx.report_progress(0.6, 1.0)

    body = resp.json()
    total_available = int(body.get("nbHits") or 0)
    hits = parse_search_response(body)

    msg = (
        f"hn: parsed {len(hits)} hits "
        f"(total available: {total_available}, sort={req.sort_by})"
    )
    if ctx:
        await ctx.info(msg)
        await ctx.report_progress(1.0, 1.0)
    else:
        logger.info(
            "hn.search query=%r returned %d (total %d)",
            req.query, len(hits), total_available,
        )

    return hits
