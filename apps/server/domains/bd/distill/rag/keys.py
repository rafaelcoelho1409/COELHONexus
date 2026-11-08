"""Object and Redis lock keys for the separate Distill retrieval index."""
from __future__ import annotations

from .. import keys as distill_keys


def index_manifest_key(book_id: str, run_id: str) -> str:
    return f"{distill_keys.run_prefix(book_id, run_id)}rag/index.json"


def index_lock_key(book_id: str, run_id: str) -> str:
    return f"coelhonexus:bd:distill:rag-index:{book_id}:{run_id}"


def enqueue_lock_key(book_id: str, run_id: str) -> str:
    return f"coelhonexus:bd:distill:rag-index-enqueue:{book_id}:{run_id}"
