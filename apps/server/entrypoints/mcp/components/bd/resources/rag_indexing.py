"""Resource handlers for Agentic RAG index snapshots."""
from __future__ import annotations

from ..tools.rag_indexing import service


class RetrievalIndexResources:
    def __init__(self, *, trusted_in_process: bool = False):
        self.trusted_in_process = trusted_in_process

    async def run(self, book_id: str, agentic_run_id: str) -> dict:
        """Return progress and index counts without backend collection names."""
        return await service.read_run(book_id, agentic_run_id, self.trusted_in_process)

    async def current(self, book_id: str) -> dict:
        """Return the currently published index snapshot."""
        return await service.read_current_index(book_id, self.trusted_in_process)
