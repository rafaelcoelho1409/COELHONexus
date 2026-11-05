from __future__ import annotations


def event_channel(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:ingestion:{book_id}:agentic:{agentic_run_id}:events"


def snapshot_key(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:ingestion:{book_id}:agentic:{agentic_run_id}:snapshot"


def cancel_key(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:ingestion:{book_id}:agentic:{agentic_run_id}:cancel"


def book_mutation_lock_key(book_id: str) -> str:
    return f"coelhonexus:bd:lock:book-mutation:{book_id}"


def run_mutation_lock_key(book_id: str, agentic_run_id: str) -> str:
    return f"coelhonexus:bd:lock:agentic-mutation:{book_id}:{agentic_run_id}"
