"""OpenAlex MCP tool — boundary layer.

Per docs/CODE-CONVENTIONS.md §4: THIN shell. @mcp.tool binds the Pydantic
schema and async signature; the body is one call into service.search_openalex
with error→ToolError mapping at the boundary. Same convention-locked
layout as arxiv/tool.py + semantic_scholar/tool.py.

  openalex/
  ├── tool.py     ← THIS — @mcp.tool boundary + ToolError mapping
  ├── service.py    async httpx + ctx logging/progress
  ├── domain.py     PURE: parse OpenAlex JSON (incl. abstract reconstruction) → list[Paper]
  ├── schemas.py    Pydantic SearchInput + Paper (OpenAlex-specific shape)
  ├── config.py     frozen-dataclass OpenAlexConfig
  └── keys.py       TOOL_NAME

Added 2026-09-20 as a 5th discovery source: OpenAlex has no API key, no
hard rate limit for polite (mailto-tagged) callers, and 320M+ works —
structurally avoids both failure modes the other 4 tools hit repeatedly
(arxiv's 406 load-shedding under concurrency, S2's contested shared pool).
"""
from __future__ import annotations
import middleware
from . import config, keys, schemas, service

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError


def register(mcp: FastMCP) -> None:
    """Register the openalex_search tool on the given FastMCP server.

    Also declares the per-tool min-interval to the cross-cutting
    RateLimitMiddleware so the wait happens BEFORE the tool body runs.
    """
    middleware.ratelimit.register(keys.TOOL_NAME, config.OPENALEX.min_request_interval_s)

    @mcp.tool(name=keys.TOOL_NAME)
    async def openalex_search(
        ctx:              Context,
        query:            str,
        n_max:            int             = 20,
        year_min:         int      | None = None,
        year_max:         int      | None = None,
        open_access_only: bool            = False,
    ) -> list[schemas.Paper]:
        """Search OpenAlex — a free, keyless, 320M+-work academic index —
        for works matching a free-text query.

        Returns Paper objects with OpenAlex-unique signal fields:
            - is_open_access / open_access_pdf  — free-to-read availability
            - topics                             — top subject-area names
            - external_ids                       — {DOI, PubMed, PMC} for
                                                    cross-source dedup
            - cited_by_count

        Broader recall than arxiv (matches title/abstract/fulltext, not
        just title/abstract) — expect some off-topic noise the caller
        should filter downstream, same trade-off already made for HN.

        Server-side filters (always preferred over post-filtering):
            - year_min / year_max    — publication-year range
            - open_access_only       — restrict to freely-readable works

        No API key, no documented hard rate limit for polite callers
        (requests are tagged with an email via the `mailto` param).
        """
        req = schemas.SearchInput(
            query=query,
            n_max=n_max,
            year_min=year_min,
            year_max=year_max,
            open_access_only=open_access_only,
        )
        try:
            return await service.search_openalex(req, ctx)
        except httpx.HTTPStatusError as e:
            raise ToolError(
                f"OpenAlex API returned {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ToolError(f"Network error reaching OpenAlex: {e}") from e
        except (KeyError, ValueError) as e:
            raise ToolError(f"OpenAlex response was malformed: {e}") from e
