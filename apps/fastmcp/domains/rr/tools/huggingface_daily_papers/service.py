"""I/O orchestration for the HuggingFace Daily Papers tool — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §4: async + httpx + logging here; all parsing
delegated to domain.parse_daily_papers_response (pure); rate-limiting handled
by the cross-cutting RateLimitMiddleware (declared in tool.py).

Reads top-to-bottom as the algorithm:
  resolve date → GET → parse → post-filter upvotes → trim to n_max.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import httpx

from .config import HF_DAILY
from .domain import parse_daily_papers_response
from .schemas import Paper, SearchInput

if TYPE_CHECKING:
    from fastmcp import Context


logger = logging.getLogger(__name__)


def _resolve_date(req: SearchInput) -> date:
    """Default to today (UTC) when the caller didn't specify a date.

    UTC because HF's daily curation rolls over at UTC midnight — using the
    server's local timezone would surface "yesterday's" set in some parts
    of the day.
    """
    return req.target_date or datetime.now(timezone.utc).date()


async def fetch_daily_papers(
    req: SearchInput, ctx: Context | None = None,
) -> list[Paper]:
    """Fetch HF's daily curated papers for the resolved date.

    Cross-cutting RateLimitMiddleware has already blocked us if we're inside
    the min-interval window. This body just does the HTTP, parse, post-filter.
    """
    target = _resolve_date(req)

    if ctx:
        await ctx.info(
            f"hf: fetching daily_papers for {target.isoformat()} (n_max={req.n_max})"
        )
        await ctx.report_progress(0.0, 1.0)

    params = {"date": target.isoformat()}
    headers = {"User-Agent": HF_DAILY.user_agent, "Accept": "application/json"}

    if ctx:
        await ctx.report_progress(0.25, 1.0)

    async with httpx.AsyncClient(timeout=HF_DAILY.timeout_s) as client:
        resp = await client.get(
            f"{HF_DAILY.base_url}{HF_DAILY.daily_papers_path}",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()

    if ctx:
        await ctx.info(f"hf: HTTP {resp.status_code}, {len(resp.content)} bytes")
        await ctx.report_progress(0.6, 1.0)

    body = resp.json()
    papers = parse_daily_papers_response(body)
    total_curated = len(papers)

    # Apply min_upvotes filter post-parse (HF doesn't filter server-side here).
    if req.min_upvotes is not None:
        papers = [p for p in papers if p.upvotes >= req.min_upvotes]

    # Trim to n_max. HF returns however many were curated today; n_max is a
    # client-side cap to keep responses bounded for the agent.
    if len(papers) > req.n_max:
        papers = papers[: req.n_max]

    msg = (
        f"hf: parsed {total_curated} curated papers, returning {len(papers)} "
        f"(date={target.isoformat()}, min_upvotes={req.min_upvotes})"
    )
    if ctx:
        await ctx.info(msg)
        await ctx.report_progress(1.0, 1.0)
    else:
        logger.info(
            "hf.daily date=%s curated=%d returned=%d",
            target.isoformat(), total_curated, len(papers),
        )

    return papers
