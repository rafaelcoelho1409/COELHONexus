"""Resource handlers for Agentic Ingestion run and quality snapshots."""
from __future__ import annotations

from ..tools.ingestion import service


class IngestionResources:
    def __init__(self, *, trusted_in_process: bool = False):
        self.trusted_in_process = trusted_in_process

    async def run(self, book_id: str, agentic_run_id: str) -> dict:
        """Return compact ingestion run status without storage object keys."""
        return await service.read_run(book_id, agentic_run_id, self.trusted_in_process)

    async def quality(self, book_id: str, agentic_run_id: str) -> dict:
        """Return aggregate quality counts without page content."""
        return await service.read_quality(book_id, agentic_run_id, self.trusted_in_process)
