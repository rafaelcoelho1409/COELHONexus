"""Redis event and replay keys for Agentic RAG index runs."""

from __future__ import annotations


def event_channel(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:ingestion:retrieval-index:{book_id}:{agentic_run_id}:events"


def snapshot_key(book_id: str, agentic_run_id: str) -> str:
    return (
        f"coelhonexus:bd:ingestion:retrieval-index:{book_id}:{agentic_run_id}:snapshot"
    )
