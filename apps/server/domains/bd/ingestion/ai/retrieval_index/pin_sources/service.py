"""Read and pin the finalized processed-book revision as newline-delimited JSON."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import domains
import infra

from .. import params
from . import domain


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def pin_sources(
    state: dict[str, Any],
    storage: infra.minio.service.MinIOStorage,
    *,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    book_id = state.get("book_id")
    run_id = state.get("agentic_run_id")
    if not isinstance(book_id, str) or not isinstance(run_id, str):
        raise ValueError("source pinning requires a book and Agentic Ingestion run")
    run = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, run_id, storage = storage)
    if run is None or run.get("status") != "done":
        raise ValueError("retrieval indexing requires a completed Ingestion run")
    current = await domains.bd.ingestion.ai.processed_book.service.read_current(
        book_id,
        run_id,
        storage = storage,
    )
    if current is None:
        raise ValueError("retrieval indexing requires a published processed book")
    processed, processed_key, processed_sha256 = current
    if processed.extraction_run_id != run.get("extraction_run_id"):
        raise ValueError("the processed book does not match the completed Ingestion run")

    rows = []
    if progress_callback is not None:
        await progress_callback(0, len(processed.pages))
    for page_index, page in enumerate(processed.pages, start = 1):
        if cancel_check is not None and await cancel_check():
            raise RuntimeError("processed-book retrieval indexing was cancelled")
        content = await storage.read_text(page.content_key)
        if _sha256(content) != page.content_sha256:
            raise ValueError(f"processed page {page.processed_page_number} failed its integrity check")
        rows.append(domain.page_record(
            book_id = book_id,
            extraction_run_id = processed.extraction_run_id,
            agentic_run_id = run_id,
            revision = processed.selection_revision,
            processed_page_number = page.processed_page_number,
            source_page_number = page.source_page_number,
            section_path = page.section_path,
            content_role = page.role,
            content_key = page.content_key,
            content_sha256 = page.content_sha256,
            content = content,
        ))
        if progress_callback is not None and (
            page_index % params.PAGE_PROGRESS_INTERVAL == 0 or page_index == len(processed.pages)
        ):
            await progress_callback(page_index, len(processed.pages))
    if not rows:
        raise ValueError("the processed book contains no pages to index")

    artifact_key = domains.bd.ingestion.ai.retrieval_index.keys.sources_key(book_id, run_id)
    payload = "\n".join(
        json.dumps(row, ensure_ascii = False, separators = (",", ":"), sort_keys = True)
        for row in rows
    )
    await storage.write(artifact_key, payload, content_type = "application/x-ndjson")
    return {
        "processed_book_key": processed_key,
        "processed_book_sha256": processed_sha256,
        "processed_book_revision": processed.selection_revision,
        "processed_page_count": len(rows),
        "pin_sources_pages_completed": len(rows),
        "source_artifact_key": artifact_key,
    }
