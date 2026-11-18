"""Stable book and page identifiers for the shared full-text index."""
from __future__ import annotations

import hashlib
from typing import Any


def book_fingerprint(book_id: str) -> str:
    """Return a stable key for grouping every indexed snapshot of one book."""
    return hashlib.sha256(f"coelhonexus:bd:book:{book_id}".encode("utf-8")).hexdigest()


def page_document_id(
    book_id: str,
    agentic_run_id: str,
    revision: int,
    page_number: int,
) -> str:
    """Return a collision-safe Elasticsearch ID for one versioned book page."""
    identity = f"coelhonexus:bd:page:{book_id}:{agentic_run_id}:{revision}:{page_number}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def dispatch_to_stores(_state: dict[str, Any]) -> list[str]:
    """Fan out the fully indexed page set to vector and knowledge-graph projections."""
    return ["qdrant", "neo4j"]
