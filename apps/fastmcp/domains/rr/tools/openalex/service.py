"""I/O orchestration for the OpenAlex tool — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §4: async + httpx + logging here; all parsing
delegated to domain.parse_search_response (pure); rate-limiting handled by
the cross-cutting RateLimitMiddleware (declared in tool.py).

Reads top-to-bottom as the algorithm:
  build params (+ mailto polite-pool tag) → GET → parse → return.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from .config import OPENALEX
from .domain import parse_search_response
from .schemas import Paper, SearchInput

if TYPE_CHECKING:
    from fastmcp import Context


logger = logging.getLogger(__name__)


def _build_params(req: SearchInput) -> dict[str, str | int]:
    """Compose the /works querystring. Filters are server-side via
    OpenAlex's comma-separated `filter=` param."""
    params: dict[str, str | int] = {
        "search": req.query,
        "per_page": min(req.n_max, OPENALEX.max_results_per_call),
        "sort": "publication_date:desc",
        # Polite pool — a query param, not a header, per OpenAlex's docs.
        "mailto": OPENALEX.mailto,
    }

    filters: list[str] = []
    if req.year_min is not None:
        filters.append(f"from_publication_date:{req.year_min}-01-01")
    if req.year_max is not None:
        filters.append(f"to_publication_date:{req.year_max}-12-31")
    if req.open_access_only:
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)

    return params


async def search_openalex(req: SearchInput, ctx: Context | None = None) -> list[Paper]:
    """Search OpenAlex's /works. The cross-cutting RateLimitMiddleware has
    already paced us before this function runs."""
    if ctx:
        await ctx.info(f"openalex: searching {req.query!r} (n_max={req.n_max})")
        await ctx.report_progress(0.0, 1.0)

    params = _build_params(req)
    headers = {"User-Agent": OPENALEX.user_agent}

    if ctx:
        await ctx.report_progress(0.25, 1.0)

    async with httpx.AsyncClient(timeout=OPENALEX.timeout_s) as client:
        resp = await client.get(OPENALEX.base_url, params=params, headers=headers)
        resp.raise_for_status()

    if ctx:
        await ctx.info(f"openalex: HTTP {resp.status_code}, {len(resp.content)} bytes")
        await ctx.report_progress(0.6, 1.0)

    body = resp.json()
    total_available = int((body.get("meta") or {}).get("count") or 0)
    papers = parse_search_response(body)

    msg = f"openalex: parsed {len(papers)} papers (total available: {total_available})"
    if ctx:
        await ctx.info(msg)
        await ctx.report_progress(1.0, 1.0)
    else:
        logger.info(
            "openalex.search query=%r returned %d (total %d)",
            req.query, len(papers), total_available,
        )

    return papers
