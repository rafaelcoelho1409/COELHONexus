"""Check the processed-book revision and publish its completed index pointer."""
from __future__ import annotations

import json
import time
from typing import Any

import domains
import infra

from . import domain


async def publish_index(
    state: dict[str, Any],
    storage: infra.minio.service.MinIOStorage,
) -> dict[str, Any]:
    book_id = state["book_id"]
    run_id = state["agentic_run_id"]
    current = await domains.bd.ingestion.ai.processed_book.service.read_current(
        book_id,
        run_id,
        storage = storage,
    )
    if current is None:
        raise ValueError("the processed-book revision disappeared before index publication")

    manifest = await domains.bd.rag.service.read_index(
        book_id,
        run_id,
        storage = storage,
    )
    if manifest is None:
        raise ValueError("the retrieval-index run manifest is missing")

    pointer = domain.published_pointer(
        state,
        current_processed_book = current,
        manifest_key = domains.bd.rag.keys.manifest_key(book_id, run_id),
        published_at = time.time(),
    )
    await storage.write(
        domains.bd.ingestion.source.storage.keys.current_retrieval_index_key(book_id),
        json.dumps(pointer, ensure_ascii = False, separators = (",", ":"), sort_keys = True),
        content_type = "application/json",
    )
    return {"published": True}
