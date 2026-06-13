"""HuggingFace Daily Papers MCP tool — boundary layer.

Per docs/CODE-CONVENTIONS.md §4: THIN shell. @mcp.tool binds the Pydantic
schema and async signature; the body is one call into service.fetch_daily_papers
with error→ToolError mapping. Same convention-locked layout as arxiv/tool.py
and semantic_scholar/tool.py.

  huggingface_daily_papers/
  ├── tool.py     ← THIS — @mcp.tool boundary + ToolError mapping
  ├── service.py    async httpx + ctx logging/progress
  ├── domain.py     PURE: parse HF JSON → list[Paper]
  ├── schemas.py    Pydantic SearchInput + Paper (HF-specific shape)
  └── config.py     frozen-dataclass HuggingFaceDailyPapersConfig
"""
from __future__ import annotations

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from middleware import ratelimit

from datetime import date

from .config import HF_DAILY
from .schemas import Paper, SearchInput
from .service import fetch_daily_papers


def register(mcp: FastMCP) -> None:
    """Register the huggingface_daily_papers tool on the FastMCP server.

    Also declares the per-tool min-interval to the cross-cutting
    RateLimitMiddleware (1 s polite default — HF doesn't enforce a documented
    cap on this endpoint, no auth needed at all).
    """
    ratelimit.register("huggingface_daily_papers", HF_DAILY.min_request_interval_s)

    @mcp.tool(name="huggingface_daily_papers")
    async def huggingface_daily_papers(
        ctx:         Context,
        target_date: date | None = None,
        n_max:       int        = 20,
        min_upvotes: int | None = None,
    ) -> list[Paper]:
        """Fetch HuggingFace's CURATED daily papers feed for a given date.

        UNLIKE arxiv / semantic_scholar, this is NOT a search tool — there is
        no text query. The endpoint returns whatever the HF community curated
        as "today's papers" (~10-30 papers per day, sometimes more). Use the
        `target_date` parameter to fetch a specific day's curation; defaults
        to today (UTC).

        Returns Paper objects with HF-unique signal fields:
            - upvotes              — community upvote count (the killer signal
                                     pure-arxiv lacks)
            - num_comments         — HF discussion-thread comment count
            - discussion_id        — HF discussion thread identifier
            - arxiv_id             — for cross-source dedup with the arxiv tool
            - hf_url / arxiv_url / pdf_url — handy links

        Post-filters:
            - min_upvotes          — drop papers below the threshold
            - n_max                — cap returned count (HF returns all curated)

        Rate limit: 1 req/s polite default. No API key required, no auth.
        """
        req = SearchInput(
            target_date = target_date,
            n_max       = n_max,
            min_upvotes = min_upvotes,
        )
        try:
            return await fetch_daily_papers(req, ctx)
        except httpx.HTTPStatusError as e:
            raise ToolError(
                f"HuggingFace API returned {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ToolError(f"Network error reaching HuggingFace: {e}") from e
        except (KeyError, ValueError) as e:
            raise ToolError(f"HuggingFace response was malformed: {e}") from e
