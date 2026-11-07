from __future__ import annotations


def channel(book_id: str, run_id: str) -> str:
    return f"bd:distill:{book_id}:{run_id}:events"


def snapshot(book_id: str, run_id: str) -> str:
    return f"bd:distill:{book_id}:{run_id}:snapshot"


def cancel(book_id: str, run_id: str) -> str:
    return f"bd:distill:{book_id}:{run_id}:cancel"
