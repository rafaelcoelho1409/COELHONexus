"""FastMCP registration boundary for BD Search tools."""

from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import params, schemas, tools

_READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True
)
_OPEN_WORLD = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register_tools(mcp: FastMCP) -> None:
    """Register the BD retrieval tools."""
    handlers = tools.BDSearchTools()

    @mcp.tool(
        name="bd_search_elasticsearch",
        description="Search the current published BD book index with bounded BM25 retrieval.",
        annotations=_READ_ONLY,
        timeout=params.RETRIEVAL_TIMEOUT_S,
    )
    async def search_elasticsearch(
        book_id: str, run_id: str, query: str
    ) -> schemas.RetrievalResult:
        """Search indexed book passages with BM25 retrieval.

        Args:
            book_id: BD book identifier whose published index should be searched.
            run_id: Agentic Ingestion run identifier that owns the indexed book.
            query: Search query for lexical retrieval.
        """
        return await handlers.search_elasticsearch(book_id, run_id, query)

    @mcp.tool(
        name="bd_search_qdrant",
        description="Search the current published BD book index with dense and sparse hybrid retrieval.",
        annotations=_READ_ONLY,
        timeout=params.RETRIEVAL_TIMEOUT_S,
    )
    async def search_qdrant(
        book_id: str, run_id: str, query: str
    ) -> schemas.RetrievalResult:
        """Search indexed book passages with dense and sparse hybrid retrieval.

        Args:
            book_id: BD book identifier whose published index should be searched.
            run_id: Agentic Ingestion run identifier that owns the indexed book.
            query: Search query for hybrid retrieval.
        """
        return await handlers.search_qdrant(book_id, run_id, query)

    @mcp.tool(
        name="bd_search_neo4j",
        description="Retrieve entity and relationship evidence from the current published BD book graph.",
        annotations=_READ_ONLY,
        timeout=params.RETRIEVAL_TIMEOUT_S,
    )
    async def search_neo4j(
        book_id: str, run_id: str, query: str
    ) -> schemas.RetrievalResult:
        """Retrieve entity and relationship evidence from the indexed book graph.

        Args:
            book_id: BD book identifier whose published graph should be searched.
            run_id: Agentic Ingestion run identifier that owns the indexed book.
            query: Search query for graph retrieval.
        """
        return await handlers.search_neo4j(book_id, run_id, query)

    @mcp.tool(
        name="bd_search_web",
        description="Search the selected Settings Search MCP provider for bounded external context.",
        annotations=_OPEN_WORLD,
        timeout=params.WEB_SEARCH_TIMEOUT_S,
    )
    async def search_web(query: str) -> schemas.RetrievalResult:
        """Search external sources for context beyond the published BD index.

        Args:
            query: Short web search query for external context.
        """
        return await handlers.search_web(query)
