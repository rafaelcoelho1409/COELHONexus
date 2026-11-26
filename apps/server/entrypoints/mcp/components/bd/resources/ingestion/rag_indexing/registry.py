"""Register BD Agentic RAG indexing resource templates."""
from __future__ import annotations
from . import resources

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    """Register the indexing-run and current-index resource templates."""
    handlers = resources.RetrievalIndexResources()

    @mcp.resource(
        "bd://books/{book_id}/rag/index-runs/{agentic_run_id}",
        name="bd_rag_index_run",
        title="BD Agentic RAG Index Run",
        description="Progress and backend counts for one processed-book indexing run.",
        mime_type="application/json",
        tags={"bd", "rag", "index", "run"},
    )
    async def rag_index_run(book_id: str, agentic_run_id: str) -> dict:
        """Return progress and index counts without backend collection names.

        Args:
            book_id: BD book identifier that owns the indexing run.
            agentic_run_id: Agentic RAG indexing run identifier to read.
        """
        return await handlers.run(book_id, agentic_run_id)

    @mcp.resource(
        "bd://books/{book_id}/rag/current-index",
        name="bd_rag_current_index",
        title="BD Current Retrieval Index",
        description="Summary of the validated index currently used by BD retrieval.",
        mime_type="application/json",
        tags={"bd", "rag", "index"},
    )
    async def rag_current_index(book_id: str) -> dict:
        """Return the currently published index snapshot.

        Args:
            book_id: BD book identifier whose current index should be read.
        """
        return await handlers.current(book_id)
