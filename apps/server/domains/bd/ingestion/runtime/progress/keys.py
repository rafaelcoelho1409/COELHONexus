from __future__ import annotations


def event_channel(book_id: str) -> str:
    return f"coelhonexus:bd:ingestion:{book_id}:events"


def snapshot_key(book_id: str) -> str:
    return f"coelhonexus:bd:ingestion:{book_id}:snapshot"


def progress_key(book_id: str) -> str:
    return f"coelhonexus:bd:ingestion:{book_id}:progress"


def cancel_key(book_id: str) -> str:
    return f"coelhonexus:bd:ingestion:{book_id}:cancel"


def lock_key(book_id: str) -> str:
    return f"coelhonexus:bd:lock:{book_id}"


def lock_prefix() -> str:
    """Scan-match prefix for `lock_key` — must stay an exact prefix of it, or active-run scans silently miss."""
    return "coelhonexus:bd:lock:"


def book_keys_pattern(book_id: str) -> str:
    """Glob that matches the progress, snapshot and cancel keys of one book — what a wipe deletes (the lock is released, not deleted)."""
    return f"coelhonexus:bd:ingestion:{book_id}:*"
