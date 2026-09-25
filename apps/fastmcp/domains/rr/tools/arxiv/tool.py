"""arXiv MCP tool — boundary layer.

Per docs/CODE-CONVENTIONS.md §4: this file stays a THIN shell. The @mcp.tool
decorator binds the Pydantic schema and the async signature; the body is one
call into service.search_arxiv with error→ToolError mapping at the boundary.
The conventions-compliant per-tool layout is:

    arxiv/
    ├── tool.py     ← THIS file — @mcp.tool boundary + error mapping
    ├── service.py    async httpx + rate limit + ctx logging/progress
    ├── domain.py     PURE: parse Atom XML → list[Paper]
    ├── schemas.py    Pydantic SearchInput + Paper (LLM-visible boundary)
    ├── config.py     frozen-dataclass ArxivConfig (tunables)
    └── keys.py       TOOL_NAME + ATOM_NAMESPACES

Every subsequent source tool (semantic_scholar, hn, …) copies this shape.
"""
from __future__ import annotations
from . import config, keys, schemas, service

import middleware

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError


def register(mcp: FastMCP) -> None:
    """Register the arxiv_search tool on the given FastMCP server.

    Also declares the per-tool min-interval to the cross-cutting
    RateLimitMiddleware so the wait happens BEFORE the tool body runs.
    """

    # Cross-cutting rate limit: the middleware reads from this registry per
    # call (arxiv ToS: 1 request per 3 seconds, per IP).
    middleware.ratelimit.register(keys.TOOL_NAME, config.ARXIV.min_request_interval_s)

    @mcp.tool(name=keys.TOOL_NAME)
    async def arxiv_search(
        ctx:        Context,
        query:      str,
        n_max:      int             = 20,
        sort_by:    schemas.SortBy  = "submittedDate",
        categories: list[str] | None = None,
    ) -> list[schemas.Paper]:
        """Search arXiv for recent papers matching a free-text query.

        Returns structured Paper objects (title · abstract · authors ·
        categories · publication/update dates · PDF + abs URLs · DOI ·
        author comment). Optionally filter by arXiv categories
        (e.g. 'cs.LG', 'stat.ML', 'q-fin.PR', 'math.OC').

        Sort options:
            - submittedDate (default) — newest first; best for radar mode.
            - relevance — best for one-shot lookups.
            - lastUpdatedDate — surfaces recent revisions.

        Respects arXiv's 1-request-per-3-seconds polite rate (enforced
        per-process).
        """
        req = schemas.SearchInput(
            query      = query,
            n_max      = n_max,
            sort_by    = sort_by,
            categories = categories,
        )
        try:
            return await service.search_arxiv(req, ctx)
        except httpx.HTTPStatusError as e:
            raise ToolError(
                f"arXiv API returned {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ToolError(f"Network error reaching arXiv: {e}") from e
        except ValueError as e:
            # Raised by domain.parse_atom_feed on malformed XML.
            raise ToolError(f"arXiv response was malformed: {e}") from e
