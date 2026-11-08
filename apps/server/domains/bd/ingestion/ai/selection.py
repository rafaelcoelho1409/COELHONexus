"""Versioned page-selection reads and human review overrides for BD Distill inputs."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import domains
import infra

from . import schemas


def _safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_") for character in value
    )


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


async def read_selection(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[schemas.ContentSelectionManifest, str, str] | None:
    """Return the current selection revision, its object key, and exact content hash."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    pointer_key = domains.bd.ingestion.storage.keys.agentic_current_content_selection_key(
        book_id, agentic_run_id,
    )
    if not await storage.exists(pointer_key):
        return None
    pointer = json.loads(await storage.read_text(pointer_key))
    key = pointer.get("key") if isinstance(pointer, dict) else None
    digest = pointer.get("sha256") if isinstance(pointer, dict) else None
    revision = pointer.get("revision") if isinstance(pointer, dict) else None
    if type(revision) is not int or revision < 1 or not isinstance(key, str) or not isinstance(digest, str):
        raise ValueError("the current content-selection pointer is malformed")
    expected_key = domains.bd.ingestion.storage.keys.agentic_content_selection_key(
        book_id, agentic_run_id, revision,
    )
    if key != expected_key:
        raise ValueError("the current content-selection pointer does not match its revision")
    content = await storage.read_text(key)
    actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if actual_digest != digest:
        raise ValueError("the current content-selection revision failed its integrity check")
    manifest = schemas.ContentSelectionManifest.model_validate_json(content)
    if manifest.book_id != book_id or manifest.agentic_run_id != agentic_run_id:
        raise ValueError("the current content selection belongs to a different run")
    return manifest, key, actual_digest


async def apply_overrides(
    book_id: str,
    agentic_run_id: str,
    overrides: list[dict[str, Any]],
    *,
    expected_revision: int,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> schemas.ContentSelectionManifest:
    """Append a reviewed selection revision and only then move its current pointer."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        return await _apply_overrides_unlocked(
            book_id, agentic_run_id, overrides, expected_revision, storage,
        )


async def _apply_overrides_unlocked(
    book_id: str,
    agentic_run_id: str,
    overrides: list[dict[str, Any]],
    expected_revision: int,
    storage,
):
    current = await read_selection(book_id, agentic_run_id, storage = storage)
    if current is None:
        raise FileNotFoundError("the ingestion run has no content selection")
    manifest, _, _ = current
    if manifest.revision != expected_revision:
        raise ValueError("the content selection changed; reload the latest revision before saving")
    known_pages = {page.page_number: page for page in manifest.pages}
    override_by_page: dict[int, str] = {}
    for row in overrides:
        page_number = row.get("page_number")
        disposition = row.get("disposition")
        if type(page_number) is not int or page_number not in known_pages:
            raise ValueError("an override references a page outside this content selection")
        if disposition not in {"include", "exclude"}:
            raise ValueError("page disposition must be include or exclude")
        if page_number in override_by_page:
            raise ValueError("page selection overrides must be unique")
        override_by_page[page_number] = disposition

    if not override_by_page:
        return manifest

    pages = []
    for page in manifest.pages:
        disposition = override_by_page.get(page.page_number)
        if disposition is None:
            pages.append(page)
        else:
            pages.append(page.model_copy(update = {
                "disposition": disposition,
                "decision_source": "user",
                "rationale": "Inclusion was changed by a reviewer.",
            }))
    if not any(page.disposition == "include" for page in pages):
        raise ValueError("at least one page must remain included in the Distill selection")
    revision = manifest.revision + 1
    key = domains.bd.ingestion.storage.keys.agentic_content_selection_key(
        book_id, agentic_run_id, revision,
    )
    # A crash after writing the immutable revision but before moving the
    # current pointer can leave an unreferenced object. Skip that object on
    # retry rather than making the review workflow permanently unwritable.
    while await storage.exists(key):
        revision += 1
        key = domains.bd.ingestion.storage.keys.agentic_content_selection_key(
            book_id, agentic_run_id, revision,
        )

    updated = schemas.ContentSelectionManifest(
        classifier_version = manifest.classifier_version,
        book_id = manifest.book_id,
        extraction_run_id = manifest.extraction_run_id,
        agentic_run_id = manifest.agentic_run_id,
        revision = revision,
        expected_page_count = manifest.expected_page_count,
        pages = pages,
        created_at = time.time(),
    )
    content = _json_body(updated.model_dump(mode = "json"))
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    await storage.write(key, content, content_type = "application/json")
    pointer_key = domains.bd.ingestion.storage.keys.agentic_current_content_selection_key(
        book_id, agentic_run_id,
    )
    await storage.write(
        pointer_key,
        _json_body({"revision": updated.revision, "key": key, "sha256": digest}),
        content_type = "application/json",
    )
    return updated
