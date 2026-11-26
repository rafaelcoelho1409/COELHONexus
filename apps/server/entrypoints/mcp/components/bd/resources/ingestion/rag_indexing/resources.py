"""Resource handlers for Agentic RAG index snapshots."""
from __future__ import annotations
from .... import tools


class RetrievalIndexResources:
    async def run(self, book_id: str, agentic_run_id: str) -> dict:
        """Return progress and index counts without backend collection names."""
        return await tools.ingestion.rag_indexing.service.read_run(book_id, agentic_run_id)

    async def current(self, book_id: str) -> dict:
        """Return the currently published index snapshot."""
        return await tools.ingestion.rag_indexing.service.read_current_index(book_id)
