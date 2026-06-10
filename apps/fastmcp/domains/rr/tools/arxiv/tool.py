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
    └── params.py     frozen-dataclass ArxivConfig (tunables)

Every subsequent source tool (semantic_scholar, hn, …) copies this shape.
"""
from __future__ import annotations

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from middleware import ratelimit

from .config import ARXIV
from .schemas import Paper, SearchInput
from .service import search_arxiv


def register(mcp: FastMCP) -> None:
    """Register the arxiv_search tool on the given FastMCP server.

    Also declares the per-tool min-interval to the cross-cutting
    RateLimitMiddleware so the wait happens BEFORE the tool body runs.
    """

    # Cross-cutting rate limit: the middleware reads from this registry per
    # call (arxiv ToS: 1 request per 3 seconds, per IP).
    ratelimit.register("arxiv_search", ARXIV.min_request_interval_s)

    @mcp.tool(name="arxiv_search")
    async def arxiv_search(
        input: SearchInput,
        ctx: Context,
    ) -> list[Paper]:
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
        try:
            return await search_arxiv(input, ctx)
        except httpx.HTTPStatusError as e:
            raise ToolError(
                f"arXiv API returned {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ToolError(f"Network error reaching arXiv: {e}") from e
        except ValueError as e:
            # Raised by domain.parse_atom_feed on malformed XML.
            raise ToolError(f"arXiv response was malformed: {e}") from e
