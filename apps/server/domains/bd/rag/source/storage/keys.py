"""RAG source-artifact key access through Ingestion's canonical storage keys."""
from __future__ import annotations
from .... import ingestion


def sources_key(book_id: str, agentic_run_id: str) -> str:
    return ingestion.source.storage.keys.agentic_retrieval_index_sources_key(
        book_id,
        agentic_run_id,
    )
