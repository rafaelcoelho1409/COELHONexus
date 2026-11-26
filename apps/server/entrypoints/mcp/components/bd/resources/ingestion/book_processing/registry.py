"""Register BD Agentic Ingestion resource templates."""
from __future__ import annotations
from . import resources

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    """Register the ingestion run and quality resource templates."""
    handlers = resources.IngestionResources()

    @mcp.resource(
        "bd://books/{book_id}/ingestion/runs/{agentic_run_id}",
        name="bd_ingestion_run",
        title="BD Agentic Ingestion Run",
        description="Compact run status and graph progress for one Agentic Ingestion run.",
        mime_type="application/json",
        tags={"bd", "ingestion", "run"},
    )
    async def ingestion_run(book_id: str, agentic_run_id: str) -> dict:
        """Return compact ingestion run status without storage object keys.

        Args:
            book_id: BD book identifier that owns the Ingestion run.
            agentic_run_id: Agentic Ingestion run identifier to read.
        """
        return await handlers.run(book_id, agentic_run_id)

    @mcp.resource(
        "bd://books/{book_id}/ingestion/runs/{agentic_run_id}/quality",
        name="bd_ingestion_quality_summary",
        title="BD Ingestion Quality Summary",
        description="Aggregate quality counts for one completed or in-progress Ingestion run.",
        mime_type="application/json",
        tags={"bd", "ingestion", "quality"},
    )
    async def ingestion_quality_summary(book_id: str, agentic_run_id: str) -> dict:
        """Return aggregate quality counts without page content.

        Args:
            book_id: BD book identifier that owns the Ingestion run.
            agentic_run_id: Agentic Ingestion run identifier to summarize.
        """
        return await handlers.quality(book_id, agentic_run_id)
