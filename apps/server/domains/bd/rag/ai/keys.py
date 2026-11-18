"""Object-storage and Redis keys for processed-book retrieval indexing."""
from __future__ import annotations
from ... import ingestion


def manifest_key(book_id: str, agentic_run_id: str) -> str:
    return ingestion.source.storage.keys.agentic_retrieval_index_manifest_key(
        book_id,
        agentic_run_id,
    )


def lock_key(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:ingestion:retrieval-index:{book_id}:{agentic_run_id}"


def enqueue_lock_key(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:ingestion:retrieval-index-enqueue:{book_id}:{agentic_run_id}"


def cancel_key(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:ingestion:retrieval-index-cancel:{book_id}:{agentic_run_id}"


def usage_run_id(book_id: str, agentic_run_id: str) -> str:
    """Keep retrieval-index LLM counters separate from Ingestion counters for the same source run."""
    return f"retrieval-index:{book_id}:{agentic_run_id}"


def thread_id(
    book_id: str,
    extraction_run_id: str,
    agentic_run_id: str,
    workflow_version: str,
    processed_book_revision: int,
    index_profile_fingerprint: str | None = None,
    checkpoint_generation: int = 0,
) -> str:
    """Stable LangGraph checkpoint identity for one processed-book index revision."""
    base = (
        f"bd-ingestion-retrieval-index:{book_id}:{extraction_run_id}:"
        f"{agentic_run_id}:{workflow_version}:{processed_book_revision}"
    )
    if index_profile_fingerprint:
        base = f"{base}:{index_profile_fingerprint}"
    if checkpoint_generation > 0:
        base = f"{base}:generation-{checkpoint_generation}"
    return base
