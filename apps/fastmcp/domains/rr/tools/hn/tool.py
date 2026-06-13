"""Hacker News MCP tool — boundary layer.

Per docs/CODE-CONVENTIONS.md §4: THIN shell. @mcp.tool binds the Pydantic
schema and async signature; the body is one call into service.search_hn
with error→ToolError mapping. Same convention-locked layout as the other
RR tools.

  hn/
  ├── tool.py     ← THIS — @mcp.tool boundary + ToolError mapping
  ├── service.py    async httpx + ctx logging/progress
  ├── domain.py     PURE: parse Algolia JSON → list[Hit]
  ├── schemas.py    Pydantic SearchInput + Hit (HN-specific shape)
  ├── config.py     frozen-dataclass HNConfig
  ├── keys.py       DEFAULT_TAGS + VALID_TAGS tuples
  └── patterns.py   ARXIV_URL_RE + HF_PAPERS_URL_RE (cross-source dedup)
"""
from __future__ import annotations

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from middleware import ratelimit

from datetime import date

from .config import HN
from .schemas import Hit, SearchInput, SortBy
from .service import search_hn


def register(mcp: FastMCP) -> None:
    """Register the hn_search tool on the FastMCP server.

    Also declares the per-tool min-interval to the cross-cutting
    RateLimitMiddleware (0.5 s polite default — Algolia HN gives 10k req/hr,
    no auth needed at all).
    """
    ratelimit.register("hn_search", HN.min_request_interval_s)

    @mcp.tool(name="hn_search")
    async def hn_search(
        ctx:              Context,
        query:            str,
        n_max:            int             = 20,
        tags:             list[str] | None = None,
        min_points:       int       | None = None,
        min_num_comments: int       | None = None,
        since:            date      | None = None,
        sort_by:          SortBy           = "relevance",
    ) -> list[Hit]:
        """Search Hacker News via Algolia for stories matching a query.

        The news / community-traction tier of Research Radar — complements
        the academic core (arxiv · semantic_scholar · huggingface_daily_papers).
        Cross-tier correlation is the killer pattern: an arxiv paper that's
        ALSO blowing up on HN gets a points + comments enrichment via the
        extracted `arxiv_id`.

        Returns Hit objects with HN-unique signal fields:
            - points              — community upvotes (the killer signal)
            - num_comments        — discussion-thread depth
            - url                 — external link (when present)
            - story_text          — self-post body (Ask HN / Show HN)
            - tags                — Algolia tags (story · show_hn · ask_hn · …)
            - arxiv_id            — EXTRACTED from url when arxiv.org/abs/<id>
                                    or huggingface.co/papers/<id>; enables
                                    cross-source dedup
            - hn_url              — link to the HN discussion page

        Server-side filters (always preferred over post-filtering):
            - min_points          — minimum HN upvotes
            - min_num_comments    — minimum discussion depth
            - since               — earliest creation date (UTC)
            - tags                — defaults to ['story']

        Sort options:
            - relevance (default) — Algolia-ranked by HN interest score
            - date                — strictly newest-first (calls /search_by_date)

        Rate limit: 0.5 s polite default. Algolia gives 10k req/hr/IP — no
        auth required.
        """
        # Flat params on the boundary so any LLM tool-call shape works;
        # internal service still consumes the SearchInput value object.
        req = SearchInput(
            query            = query,
            n_max            = n_max,
            tags             = tags,
            min_points       = min_points,
            min_num_comments = min_num_comments,
            since            = since,
            sort_by          = sort_by,
        )
        try:
            return await search_hn(req, ctx)
        except httpx.HTTPStatusError as e:
            raise ToolError(
                f"HN Algolia returned {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ToolError(f"Network error reaching HN Algolia: {e}") from e
        except (KeyError, ValueError) as e:
            raise ToolError(f"HN Algolia response was malformed: {e}") from e
