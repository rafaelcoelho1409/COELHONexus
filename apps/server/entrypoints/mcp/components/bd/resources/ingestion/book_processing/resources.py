"""Resource handlers for Agentic Ingestion run and quality snapshots."""
from __future__ import annotations
from .... import tools


class IngestionResources:
    async def run(self, book_id: str, agentic_run_id: str) -> dict:
        """Return compact ingestion run status without storage object keys."""
        return await tools.ingestion.book_processing.service.read_run(book_id, agentic_run_id)

    async def quality(self, book_id: str, agentic_run_id: str) -> dict:
        """Return aggregate quality counts without page content."""
        return await tools.ingestion.book_processing.service.read_quality(book_id, agentic_run_id)
