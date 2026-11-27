"""Register the book readiness resource used by BD Ask workflows."""
from __future__ import annotations
from . import resources

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    """Register the current book readiness resource."""
    handlers = resources.AdaptiveAskResources()

    @mcp.resource(
        "bd://books/{book_id}/pipeline/readiness",
        name="bd_book_pipeline_readiness",
        title="BD Book Pipeline Readiness",
        description="One snapshot across source extraction, current Ingestion progress, RAG indexing, and embedding compatibility.",
        mime_type="application/json",
        tags={"bd", "pipeline", "readiness"},
    )
    async def pipeline_readiness(book_id: str) -> dict:
        """Return a bounded, storage-key-free view of the current book pipeline.

        Args:
            book_id: BD book identifier whose current readiness should be read.
        """
        return await handlers.readiness(book_id)
