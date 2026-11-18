"""Stable identifiers for dense and sparse vector records."""
from __future__ import annotations

import hashlib
import uuid


def collection_name(model: str, dimensions: int) -> str:
    fingerprint = hashlib.sha256(f"{model}:{dimensions}".encode("utf-8")).hexdigest()[:12]
    return f"bd-ingestion-{fingerprint}"


def point_id(
    book_id: str,
    agentic_run_id: str,
    revision: int,
    page: int,
    chunk: int,
    text: str,
) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bd:{book_id}:{agentic_run_id}:{revision}:{page}:{chunk}:{digest}",
    ))
