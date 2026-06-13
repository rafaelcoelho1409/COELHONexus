"""Semantic Scholar MCP tool — boundary layer.

Per docs/CODE-CONVENTIONS.md §4: THIN shell. @mcp.tool binds the Pydantic
schema and async signature; the body is one call into service.search_s2
with error→ToolError mapping at the boundary. Same convention-locked
layout as arxiv/tool.py.

  semantic_scholar/
  ├── tool.py     ← THIS — @mcp.tool boundary + ToolError mapping
  ├── service.py    async httpx + ctx logging/progress
  ├── domain.py     PURE: parse S2 JSON → list[Paper]
  ├── schemas.py    Pydantic SearchInput + Paper (S2-specific shape)
  ├── config.py     frozen-dataclass SemanticScholarConfig
  └── keys.py       DEFAULT_FIELDS + FIELDS_OF_STUDY tuples
"""
from __future__ import annotations

import os

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from middleware import ratelimit

from .config import S2
from .schemas import Paper, SearchInput
from .service import search_s2


def register(mcp: FastMCP) -> None:
    """Register the semantic_scholar_search tool on the FastMCP server.

    Also declares the per-tool min-interval to the cross-cutting
    RateLimitMiddleware. With SEMANTIC_SCHOLAR_API_KEY env var set, the
    interval drops from 3s (unauth shared pool) to 1s (keyed ~1 RPS).
    """
    has_key = bool(os.environ.get(S2.api_key_env))
    interval = (
        S2.min_request_interval_keyed_s if has_key
        else S2.min_request_interval_s
    )
    ratelimit.register("semantic_scholar_search", interval)

    @mcp.tool(name="semantic_scholar_search")
    async def semantic_scholar_search(
        ctx:                Context,
        query:              str,
        n_max:              int             = 20,
        year_min:           int      | None = None,
        year_max:           int      | None = None,
        fields_of_study:    list[str] | None = None,
        min_citation_count: int      | None = None,
        venue_filter:       list[str] | None = None,
    ) -> list[Paper]:
        """Search Semantic Scholar for papers matching a free-text query.

        Returns Paper objects with S2-unique signal fields:
            - tldr                          — pre-generated 1-sentence summary
            - influential_citation_count    — citations that substantively
                                              use the work (better than raw cites)
            - external_ids                  — {DOI, ArXiv, PubMed, MAG, ...}
                                              for cross-source dedup with arxiv
            - open_access_pdf               — direct PDF link when available

        Server-side filters (always preferred over post-filtering):
            - year_min / year_max           — publication-year range
            - fields_of_study               — e.g. ['Computer Science']
            - min_citation_count            — high-influence filter
            - venue_filter                  — e.g. ['NeurIPS', 'ICML']

        Rate limit (per S2 ToS, enforced by middleware):
            - 1 req / 3 s     when SEMANTIC_SCHOLAR_API_KEY is unset
            - 1 RPS sustained when the env var carries a free API key

        Query syntax: free text (AND), `"phrase"` exact, `+must`, `-exclude`,
        `a|b` OR.
        """
        req = SearchInput(
            query              = query,
            n_max              = n_max,
            year_min           = year_min,
            year_max           = year_max,
            fields_of_study    = fields_of_study,
            min_citation_count = min_citation_count,
            venue_filter       = venue_filter,
        )
        try:
            return await search_s2(req, ctx)
        except httpx.HTTPStatusError as e:
            raise ToolError(
                f"Semantic Scholar API returned {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ToolError(f"Network error reaching Semantic Scholar: {e}") from e
        except (KeyError, ValueError) as e:
            raise ToolError(f"Semantic Scholar response was malformed: {e}") from e
