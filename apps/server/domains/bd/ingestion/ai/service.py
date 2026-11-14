"""Run integration between completed Book Distiller extractions and the AI graph."""
from __future__ import annotations
import ai, domains, infra
from . import domain, schemas


import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from langgraph.errors import NodeCancelledError


logger = logging.getLogger(__name__)


def _safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


async def _load_run_source(
    book_id: str,
    expected_extraction_id: str | None,
    storage: infra.minio.service.MinIOStorage,
) -> tuple[dict, str, str, str, list[dict]]:
    source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if source_manifest is None:
        raise FileNotFoundError(f"book {book_id} has no ingestion manifest")
    extraction_id, request = _extraction_request(
        book_id, source_manifest, expected_extraction_id,
    )
    source_page_refs = request.page_references()
    manifest_pages = {
        page.get("page"): page for page in source_manifest.get("pages", [])
        if isinstance(page, dict) and type(page.get("page")) is int
    }
    for page_ref in source_page_refs:
        manifest_page = manifest_pages.get(page_ref["page"], {})
        section_path = manifest_page.get("section_path", [])
        page_ref["section_path"] = (
            [item for item in section_path if isinstance(item, str)]
            if isinstance(section_path, list) else []
        )
    source_manifest_key = domains.bd.ingestion.storage.keys.manifest_key(book_id)
    source_manifest_json = json.dumps(
        source_manifest, ensure_ascii = False, separators = (",", ":"), sort_keys = True,
    )
    source_manifest_sha256 = hashlib.sha256(source_manifest_json.encode("utf-8")).hexdigest()
    return (
        source_manifest,
        extraction_id,
        source_manifest_key,
        source_manifest_sha256,
        source_page_refs,
    )


def _manifest_fields(
    book_id: str,
    agentic_run_id: str,
    extraction_id: str,
    source_manifest_key: str,
    source_manifest_sha256: str,
    expected_page_count: int,
    created_at: float,
) -> dict[str, Any]:
    return {
        "workflow_version": domains.bd.ingestion.ai.versions.WORKFLOW_VERSION,
        "agentic_run_id": agentic_run_id,
        "book_id": book_id,
        "extraction_run_id": extraction_id,
        "source_manifest_key": source_manifest_key,
        "source_manifest_sha256": source_manifest_sha256,
        "expected_page_count": expected_page_count,
        "progress": _initial_progress(),
        "pages": [],
        "book_metadata_key": domains.bd.ingestion.storage.keys.agentic_book_metadata_key(
            book_id, agentic_run_id,
        ),
        "quality_report_key": domains.bd.ingestion.storage.keys.agentic_quality_report_key(
            book_id, agentic_run_id,
        ),
        "created_at": created_at,
    }


async def _write_current_run_pointer(
    storage: infra.minio.service.MinIOStorage,
    book_id: str,
    agentic_run_id: str,
    extraction_run_id: str,
) -> None:
    key = domains.bd.ingestion.storage.keys.current_agentic_run_key(book_id)
    payload = json.dumps(
        {"agentic_run_id": agentic_run_id, "extraction_run_id": extraction_run_id},
        separators = (",", ":"),
        sort_keys = True,
    )
    try:
        await storage.write(key, payload, content_type = "application/json")
    except Exception as error:
        logger.warning(
            f"[bd-agentic] current-run pointer update failed for {book_id}/{agentic_run_id}: "
            f"{type(error).__name__}: {error}"
        )


def _initial_progress() -> dict[str, Any]:
    return {
        "current_node": None,
        "node_status": {
            node: domains.bd.ingestion.ai.schemas.AgenticNodeStatus.PENDING.value
            for node in domains.bd.ingestion.ai.params.GRAPH_NODES
        },
        "audited_pages": [],
        "audit_warning_pages": [],
        "processed_pages": [],
        "active_audit_pages": [],
        "active_repair_pages": [],
        "classified_pages": [],
        "active_classification_pages": [],
    }


def _safe_error_message(error: Exception | str) -> str:
    current = error
    seen: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if isinstance(current, ValueError) and "http:// or https://" in message:
            return "The configured language-model endpoint URL is invalid. Add an http:// or https:// scheme in Settings, then retry."
        if type(current).__name__ == "UnsupportedProtocol" and (
            "missing an 'http://' or 'https://' protocol" in message
            or "unsupported protocol" in message
        ):
            return "The configured language-model endpoint URL is invalid. Add an http:// or https:// scheme in Settings, then retry."
        if isinstance(current, Exception):
            current = current.__cause__ or current.__context__
        else:
            break
    return "Agentic processing failed. Retry the run; if the issue repeats, inspect the server logs."


def automatic_run_id(extraction_run_id: str) -> str:
    if not _safe_segment(extraction_run_id):
        raise ValueError("extraction_run_id must be a safe path segment")
    return f"{domains.bd.ingestion.ai.params.AUTO_RUN_ID_PREFIX}{extraction_run_id}"


async def create_ingestion_ai_run(
    book_id: str,
    *,
    expected_extraction_id: str | None = None,
    agentic_run_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    if not _safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        return await _create_ingestion_ai_run_unlocked(
            book_id,
            expected_extraction_id = expected_extraction_id,
            agentic_run_id = agentic_run_id,
            storage = storage,
        )


async def _create_ingestion_ai_run_unlocked(
    book_id: str,
    *,
    expected_extraction_id: str | None = None,
    agentic_run_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    """Create a queued candidate manifest before dispatching its Celery task."""
    if not _safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_extraction_id is not None and not _safe_segment(expected_extraction_id):
        raise ValueError("expected_extraction_id must be a safe path segment")
    if agentic_run_id is None:
        agentic_run_id = uuid.uuid4().hex
    if not _safe_segment(agentic_run_id):
        raise ValueError("agentic_run_id must be a safe path segment")
    if storage is None:
        storage = infra.minio.service.get_storage()

    (
        _, extraction_id, source_manifest_key, source_manifest_sha256, source_page_refs,
    ) = await _load_run_source(book_id, expected_extraction_id, storage)
    active_run = await find_active_ingestion_ai_run(book_id, storage = storage)
    if active_run is not None and active_run["agentic_run_id"] != agentic_run_id:
        raise ValueError("another Agentic AI run is already active for this book")
    manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    if await storage.exists(manifest_key):
        raise ValueError("agentic_run_id already exists; use a new ID to preserve immutable run history")
    await domains.bd.ingestion.ai.progress.service.clear_cancel(book_id, agentic_run_id)

    now = time.time()
    manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest(
        **_manifest_fields(
            book_id,
            agentic_run_id,
            extraction_id,
            source_manifest_key,
            source_manifest_sha256,
            len(source_page_refs),
            now,
        ),
        status = domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED,
        updated_at = now,
    )
    await storage.write(
        manifest_key,
        json.dumps(manifest.model_dump(mode = "json"), ensure_ascii = False,
                   separators = (",", ":"), sort_keys = True),
        content_type = "application/json",
    )
    await _write_current_run_pointer(storage, book_id, agentic_run_id, extraction_id)
    return manifest.model_dump(mode = "json")


async def create_automatic_ingestion_ai_run(
    book_id: str,
    extraction_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create one idempotent automatic candidate for a completed extraction."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    agentic_run_id = automatic_run_id(extraction_run_id)
    existing = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if existing is not None:
        if existing["extraction_run_id"] != extraction_run_id:
            raise ValueError("automatic agentic run ID is already pinned to a different extraction")
        await _write_current_run_pointer(storage, book_id, agentic_run_id, extraction_run_id)
        return existing, False
    try:
        manifest = await create_ingestion_ai_run(
            book_id,
            expected_extraction_id = extraction_run_id,
            agentic_run_id = agentic_run_id,
            storage = storage,
        )
        return manifest, True
    except ValueError:
        existing = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
        if existing is None or existing["extraction_run_id"] != extraction_run_id:
            raise
        await _write_current_run_pointer(storage, book_id, agentic_run_id, extraction_run_id)
        return existing, False


async def read_current_ingestion_ai_run(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    """Read the most recently active/current candidate for the book's extraction."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if source_manifest is None or source_manifest.get("status") != domains.bd.ingestion.storage.params.STATUS_DONE:
        return None
    extraction_run_id = source_manifest.get("extraction_id")
    if not _safe_segment(extraction_run_id):
        return None
    pointer_key = domains.bd.ingestion.storage.keys.current_agentic_run_key(book_id)
    if await storage.exists(pointer_key):
        pointer = domains.bd.ingestion.storage.domain.parse_json(
            await storage.read_text(pointer_key),
            {},
        )
        if isinstance(pointer, dict) and pointer.get("agentic_run_id") is None:
            return None
        run_id = pointer.get("agentic_run_id") if isinstance(pointer, dict) else None
        if _safe_segment(run_id):
            current = await read_ingestion_ai_run(book_id, run_id, storage = storage)
            if current is not None and current.get("extraction_run_id") == extraction_run_id:
                return current
    run_ids = await storage.list_subfolders(
        domains.bd.ingestion.storage.keys.agentic_runs_prefix(book_id),
    )
    runs = await asyncio.gather(*(
        read_ingestion_ai_run(book_id, run_id, storage = storage)
        for run_id in run_ids
        if _safe_segment(run_id)
    ))
    current_runs = [
        run for run in runs
        if run is not None and run.get("extraction_run_id") == extraction_run_id
    ]
    return max(current_runs, key = lambda run: run.get("updated_at", 0.0), default = None)


async def read_current_ingestion_ai_metadata(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    """Return verified normalized display metadata for the current extraction, if available."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    run = await read_current_ingestion_ai_run(book_id, storage = storage)
    if run is None:
        return None
    key = run.get("book_metadata_key")
    expected_sha256 = run.get("book_metadata_sha256")
    if not isinstance(key, str) or not isinstance(expected_sha256, str):
        return None
    try:
        content = await storage.read_text(key)
    except FileNotFoundError:
        return None
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_sha256:
        return None
    try:
        metadata = json.loads(content)
    except ValueError:
        return None
    if not isinstance(metadata, dict):
        return None
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    return {**metadata, "title": title.strip()}


async def read_display_page(
    book_id: str,
    page_number: int,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
    source: str | None = None,
) -> tuple[str | None, str]:
    """Return a completed automatic AI page when available, otherwise the immutable Docling page.

    `source` pins one side for comparison: "docling" always reads the raw page; "agentic" reads the AI page or nothing."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    if source != "docling":
        run = await read_current_ingestion_ai_run(book_id, storage = storage)
        if run is not None and page_number in run["progress"]["processed_pages"]:
            normalized_key = domains.bd.ingestion.storage.keys.agentic_page_key(
                book_id, run["agentic_run_id"], page_number,
            )
            try:
                return await storage.read_text(normalized_key), "agentic"
            except FileNotFoundError:
                pass
        if source == "agentic":
            return None, "agentic"
    markdown = await domains.bd.ingestion.storage.service.read_page(storage, book_id, page_number)
    return markdown, "docling"


async def read_page_quality(
    book_id: str,
    page_number: int,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    """What the current Agentic AI run found and did on one page, or None when no run covered it."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    run = await read_current_ingestion_ai_run(book_id, storage = storage)
    if run is None:
        return None
    artifact = next((page for page in run["pages"] if page["page_number"] == page_number), None)
    if artifact is None or artifact["status"] not in {"done", "review_required"}:
        return None
    try:
        assessment = json.loads(await storage.read_text(artifact["assessment_key"]))
        proposals = json.loads(await storage.read_text(artifact["patch_proposals_key"]))
    except FileNotFoundError:
        return None
    auto_applied = set(proposals.get("auto_applied_issue_ids", []))
    review_required = set(proposals.get("review_required_issue_ids", []))
    proposal_by_issue = {proposal["issue_id"]: proposal for proposal in proposals.get("proposals", [])}
    return {
        "book_id": book_id,
        "agentic_run_id": run["agentic_run_id"],
        "page": page_number,
        "page_kind": assessment["page_kind"],
        "status": artifact["status"],
        "delivery_disposition": artifact["delivery_disposition"],
        "issues": [
            {
                "issue_id": issue["issue_id"],
                "code": issue["code"],
                "severity": issue["severity"],
                "description": issue["description"],
                "evidence": [evidence["excerpt"] for evidence in issue["evidence"]],
                "resolution": (
                    "auto_applied" if issue["issue_id"] in auto_applied
                    else "review" if issue["issue_id"] in review_required
                    else "none"
                ),
                "proposal": (
                    {
                        "target_text": proposal["target_text"],
                        "replacement": proposal["replacement"],
                        "rationale": proposal["rationale"],
                        "confidence": proposal["confidence"],
                        "risk": proposal["risk"],
                    }
                    if (proposal := proposal_by_issue.get(issue["issue_id"])) is not None
                    else None
                ),
            }
            for issue in assessment["issues"]
        ],
        "mechanical_fixes": proposals.get("mechanical_fixes", {}),
        "audit_warnings": proposals.get("audit_warnings", []),
        "errors": proposals.get("errors", []),
    }


async def read_ingestion_ai_run(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    """Read one persisted candidate manifest for API status polling."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    if not await storage.exists(manifest_key):
        return None
    manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest.model_validate_json(
        await storage.read_text(manifest_key),
    )
    if manifest.book_id != book_id or manifest.agentic_run_id != agentic_run_id:
        raise ValueError("the stored agentic manifest does not match the requested run")
    return manifest.model_dump(mode = "json")


async def read_selection(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[schemas.ContentSelectionManifest, str, str] | None:
    """Return the current page-selection revision, key, and exact content hash."""
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


async def ensure_selection(
    book_id: str,
    agentic_run_id: str,
    *,
    run_manifest: dict[str, Any] | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[schemas.ContentSelectionManifest, str, str]:
    """Return a selection, migrating completed pre-classifier runs with a conservative default.

    Older completed runs have no content-selection artifact. Keep known blank pages excluded and
    retain every other page as unknown so a compatibility migration can never silently drop book
    content. The generated artifact is immutable and reviewed edits continue through revisions.
    """
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
        current = await read_selection(book_id, agentic_run_id, storage = storage)
        if current is not None:
            return current

        manifest = run_manifest or await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError(f"Agentic ingestion run {agentic_run_id!r} was not found")
        if manifest.get("book_id") != book_id or manifest.get("agentic_run_id") != agentic_run_id:
            raise ValueError("the ingestion run does not match the requested content selection")
        if manifest.get("status") != schemas.AgenticRunStatus.DONE.value:
            raise ValueError("content selection is available after ingestion completes")

        revision = 1
        while True:
            key = domains.bd.ingestion.storage.keys.agentic_content_selection_key(
                book_id, agentic_run_id, revision,
            )
            if not await storage.exists(key):
                selection = domain.build_legacy_selection(manifest, revision, time.time())
                content = domain.selection_json(selection)
                await storage.write(key, content, content_type = "application/json")
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                break

            content = await storage.read_text(key)
            try:
                existing = schemas.ContentSelectionManifest.model_validate_json(content)
            except ValueError:
                revision += 1
                continue
            if (
                existing.book_id != book_id
                or existing.agentic_run_id != agentic_run_id
                or existing.extraction_run_id != manifest.get("extraction_run_id")
                or existing.revision != revision
                or existing.expected_page_count != manifest.get("expected_page_count")
            ):
                revision += 1
                continue
            selection = existing
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            break

        pointer_key = domains.bd.ingestion.storage.keys.agentic_current_content_selection_key(
            book_id, agentic_run_id,
        )
        await storage.write(
            pointer_key,
            json.dumps(
                {"revision": selection.revision, "key": key, "sha256": digest},
                separators = (",", ":"),
                sort_keys = True,
            ),
            content_type = "application/json",
        )
        return selection, key, digest


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
        return await _apply_selection_overrides_unlocked(
            book_id, agentic_run_id, overrides, expected_revision, storage,
        )


async def _apply_selection_overrides_unlocked(
    book_id: str,
    agentic_run_id: str,
    overrides: list[dict[str, Any]],
    expected_revision: int,
    storage: infra.minio.service.MinIOStorage,
) -> schemas.ContentSelectionManifest:
    current = await read_selection(book_id, agentic_run_id, storage = storage)
    if current is None:
        raise FileNotFoundError("the ingestion run has no content selection")
    manifest, _, _ = current
    updated = domain.apply_selection_overrides(
        manifest,
        overrides,
        expected_revision = expected_revision,
        revision = manifest.revision + 1,
        created_at = time.time(),
    )
    if updated is manifest:
        return manifest

    revision = updated.revision
    key = domains.bd.ingestion.storage.keys.agentic_content_selection_key(
        book_id, agentic_run_id, revision,
    )
    # A crash after writing the immutable revision but before moving the
    # current pointer can leave an unreferenced object. Skip it on retry.
    while await storage.exists(key):
        revision += 1
        key = domains.bd.ingestion.storage.keys.agentic_content_selection_key(
            book_id, agentic_run_id, revision,
        )
    if revision != updated.revision:
        updated = updated.model_copy(update = {"revision": revision})

    content = domain.selection_json(updated)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    await storage.write(key, content, content_type = "application/json")
    processed, processed_key, processed_sha256 = await domains.bd.ingestion.ai.processed_book.service.ensure_for_selection(
        book_id,
        agentic_run_id,
        updated,
        key,
        digest,
        storage = storage,
    )
    pointer_key = domains.bd.ingestion.storage.keys.agentic_current_content_selection_key(
        book_id, agentic_run_id,
    )
    await storage.write(
        pointer_key,
        json.dumps(
            {"revision": updated.revision, "key": key, "sha256": digest},
            separators = (",", ":"),
            sort_keys = True,
        ),
        content_type = "application/json",
    )
    await domains.bd.ingestion.ai.processed_book.service.write_current_pointer(
        book_id,
        agentic_run_id,
        processed.selection_revision,
        processed_key,
        processed_sha256,
        storage,
    )
    return updated


async def find_active_ingestion_ai_run(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
    exclude_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Find any queued/running candidate, not only the run currently selected for display."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    run_ids = await storage.list_subfolders(
        domains.bd.ingestion.storage.keys.agentic_runs_prefix(book_id),
    )
    for run_id in run_ids:
        if not _safe_segment(run_id) or run_id == exclude_run_id:
            continue
        manifest = await read_ingestion_ai_run(book_id, run_id, storage = storage)
        if manifest is not None and manifest["status"] in {
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED.value,
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.RUNNING.value,
        }:
            return manifest
    return None


async def cancel_ingestion_ai_run(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    """Request a cooperative stop; active graph work drains to the next page or node boundary."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_ingestion_ai_run(book_id, agentic_run_id)
        if manifest is None:
            raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
        if manifest["status"] not in {
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED.value,
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.RUNNING.value,
        }:
            return {
                "book_id": book_id,
                "agentic_run_id": agentic_run_id,
                "status": manifest["status"],
            }
        await domains.bd.ingestion.ai.progress.service.request_cancel(book_id, agentic_run_id)
        await domains.bd.ingestion.ai.progress.service.emit(
            book_id,
            agentic_run_id,
            "run_cancelling",
            status = "cancelling",
            progress = manifest["progress"],
        )
        return {"book_id": book_id, "agentic_run_id": agentic_run_id, "status": "cancelling"}


async def delete_ingestion_ai_checkpoints(
    manifests: list[dict[str, Any]],
    *,
    include_inventory: bool = False,
) -> int:
    """Delete graph and page-audit checkpoints pinned to the supplied candidate manifests."""
    thread_ids: set[str] = set()
    for manifest in manifests:
        book_id = manifest["book_id"]
        extraction_id = manifest["extraction_run_id"]
        agentic_run_id = manifest["agentic_run_id"]
        thread_ids.add(f"bd-ingestion-agentic:{book_id}:{extraction_id}:{agentic_run_id}")
        thread_ids.update(
            f"bd-ingestion-page-audit:{book_id}:{extraction_id}:{page_no}"
            for page_no in range(1, manifest["expected_page_count"] + 1)
        )
        if include_inventory:
            thread_ids.add(f"bd-ingestion-inventory:{book_id}:{extraction_id}")
    if not thread_ids:
        return 0
    checkpointer = await infra.postgres.service.init_checkpointer()
    semaphore = asyncio.Semaphore(8)

    async def delete_thread(thread_id: str) -> None:
        async with semaphore:
            await checkpointer.adelete_thread(thread_id)

    await asyncio.gather(*(delete_thread(thread_id) for thread_id in sorted(thread_ids)))
    return len(thread_ids)


async def wipe_ingestion_ai_run(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    """Delete one terminal candidate and its AI-only artifacts, counters, replay, and checkpoints."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 3600,
            blocking_timeout = 30,
        ),
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
            timeout = 3600,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
        if manifest["status"] in {
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED.value,
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.RUNNING.value,
        }:
            raise ValueError("stop the Agentic AI run and wait for it to finish before wiping it")
        active_run = await find_active_ingestion_ai_run(
            book_id,
            storage = storage,
            exclude_run_id = agentic_run_id,
        )
        if active_run is not None:
            raise ValueError("stop the other active Agentic AI run before wiping this run")
        await delete_ingestion_ai_checkpoints([manifest])
        removed = await storage.delete_prefix(
            domains.bd.ingestion.storage.keys.agentic_prefix(book_id, agentic_run_id),
        )
        await domains.settings.llm_counter.service.delete_run("bd", agentic_run_id)
        await redis.delete(
            domains.bd.ingestion.ai.progress.keys.cancel_key(book_id, agentic_run_id),
            domains.bd.ingestion.ai.progress.keys.snapshot_key(book_id, agentic_run_id),
        )
        pointer_key = domains.bd.ingestion.storage.keys.current_agentic_run_key(book_id)
        if await storage.exists(pointer_key):
            pointer = domains.bd.ingestion.storage.domain.parse_json(
                await storage.read_text(pointer_key),
                {},
            )
            if isinstance(pointer, dict) and pointer.get("agentic_run_id") == agentic_run_id:
                await storage.write(
                    pointer_key,
                    json.dumps(
                        {
                            "agentic_run_id": None,
                            "extraction_run_id": manifest["extraction_run_id"],
                        },
                        separators = (",", ":"),
                        sort_keys = True,
                    ),
                    content_type = "application/json",
                )
        return {
            "book_id": book_id,
            "agentic_run_id": agentic_run_id,
            "wiped": True,
            "objects_deleted": removed,
        }


async def queue_ingestion_ai_resume(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        return await _queue_ingestion_ai_resume_unlocked(book_id, agentic_run_id, storage = storage)


async def _queue_ingestion_ai_resume_unlocked(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    """Requeue a failed candidate with the same source identity and graph thread."""
    manifest = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if manifest is None:
        raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
    if manifest["status"] not in {
        domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED.value,
        domains.bd.ingestion.ai.schemas.AgenticRunStatus.CANCELLED.value,
    }:
        raise ValueError("only failed or cancelled agentic runs can be resumed")
    if storage is None:
        storage = infra.minio.service.get_storage()
    active_run = await find_active_ingestion_ai_run(
        book_id,
        storage = storage,
        exclude_run_id = agentic_run_id,
    )
    if active_run is not None:
        raise ValueError("stop the other active Agentic AI run before resuming this one")
    _, extraction_id, _, source_manifest_sha256, _ = await _load_run_source(
        book_id, manifest["extraction_run_id"], storage,
    )
    if extraction_id != manifest["extraction_run_id"] or source_manifest_sha256 != manifest["source_manifest_sha256"]:
        raise ValueError("the failed agentic run no longer matches the current extraction")
    await domains.bd.ingestion.ai.progress.service.clear_cancel(book_id, agentic_run_id)
    await domains.bd.ingestion.ai.progress.service.clear_events(book_id, agentic_run_id)
    manifest["status"] = domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED.value
    manifest["updated_at"] = max(time.time(), manifest["created_at"])
    manifest["finished_at"] = None
    manifest["error"] = None
    manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    await storage.write(
        manifest_key,
        json.dumps(manifest, ensure_ascii = False, separators = (",", ":"), sort_keys = True),
        content_type = "application/json",
    )
    await _write_current_run_pointer(
        storage,
        book_id,
        agentic_run_id,
        manifest["extraction_run_id"],
    )
    return manifest


async def mark_ingestion_ai_run_failed(
    book_id: str,
    agentic_run_id: str,
    error: Exception | str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> None:
    """Persist a task-level failure, including failures before the graph starts."""
    manifest = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if manifest is None or manifest["status"] in {
        domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE.value,
        domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED.value,
        domains.bd.ingestion.ai.schemas.AgenticRunStatus.CANCELLED.value,
    }:
        return
    if storage is None:
        storage = infra.minio.service.get_storage()
    finished_at = max(time.time(), manifest["created_at"])
    error_text = _safe_error_message(error)
    manifest["status"] = domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED.value
    manifest["updated_at"] = finished_at
    manifest["finished_at"] = finished_at
    manifest["error"] = error_text[:500]
    progress = manifest.get("progress") or _initial_progress()
    progress["active_audit_pages"] = []
    progress["active_repair_pages"] = []
    current_node = progress.get("current_node")
    if not current_node:
        current_node = next(
            (
                node for node in domains.bd.ingestion.ai.params.GRAPH_NODES
                if progress.get("node_status", {}).get(node) != domains.bd.ingestion.ai.schemas.AgenticNodeStatus.DONE.value
            ),
            None,
        )
    if current_node in progress.get("node_status", {}):
        progress["node_status"][current_node] = domains.bd.ingestion.ai.schemas.AgenticNodeStatus.FAILED.value
    progress["current_node"] = current_node
    manifest["progress"] = progress
    manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    await storage.write(
        manifest_key,
        json.dumps(manifest, ensure_ascii = False, separators = (",", ":"), sort_keys = True),
        content_type = "application/json",
    )


def _extraction_request(
    book_id: str,
    manifest: dict,
    expected_extraction_id: str | None,
) -> tuple[str, domains.bd.ingestion.ai.schemas.ExtractionRequest]:
    if manifest.get("book_id") != book_id:
        raise ValueError("the manifest book_id does not match the requested book")

    extraction_id = manifest.get("extraction_id")
    if not _safe_segment(extraction_id):
        raise ValueError("the manifest has no safe extraction_id")
    if expected_extraction_id is not None and extraction_id != expected_extraction_id:
        raise ValueError("the requested extraction_id is no longer current")
    if manifest.get("status") != domains.bd.ingestion.storage.params.STATUS_DONE:
        raise ValueError("only completed extraction runs can be inventoried")

    page_count = manifest.get("n_pages")
    pages = manifest.get("pages")
    if type(page_count) is not int or page_count <= 0:
        raise ValueError("the manifest must contain a positive n_pages value")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise ValueError("the completed manifest must contain every extracted page")
    if any(not isinstance(page, dict) for page in pages):
        raise ValueError("every manifest page entry must be an object")

    request = domains.bd.ingestion.ai.schemas.ExtractionRequest(
        book_id = book_id,
        extraction_run_id = extraction_id,
        extraction_status = manifest["status"],
        expected_page_count = page_count,
        page_refs = [
            {
                "page": page.get("page"),
                "key": page.get("key"),
                "pictures": page.get("pictures", 0),
            }
            for page in pages
        ],
    )
    return extraction_id, request


async def run_page_audit(
    book_id: str,
    page_number: int,
    *,
    expected_extraction_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
    auditor: Any | None = None,
    finding_verifier: Any | None = None,
    content_classifier: Any | None = None,
) -> dict[str, Any]:
    """Audit one page of the current completed extraction the way the full run does, and write nothing.

    It calls the same `audit_page` as the full-book audit, so the auditor evaluation measures the real path. Pass
    `finding_verifier` to include the second reader."""
    if not _safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_extraction_id is not None and not _safe_segment(expected_extraction_id):
        raise ValueError("expected_extraction_id must be a safe path segment")
    if type(page_number) is not int or page_number <= 0:
        raise ValueError("page_number must be a positive integer")
    if storage is None:
        storage = infra.minio.service.get_storage()

    manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if manifest is None:
        raise FileNotFoundError(f"book {book_id} has no ingestion manifest")

    _, request = _extraction_request(book_id, manifest, expected_extraction_id)
    page_refs = request.page_references()
    if page_number > len(page_refs):
        raise ValueError("page_number is outside the completed extraction")
    page_ref = page_refs[page_number - 1]
    markdown = await storage.read_text(page_ref["key"])
    if auditor is None:
        auditor = ai.bd.agents.ingestion.page_auditor.agent.page_auditor()

    audited = await ai.bd.nodes.ingestion.audit_batch.service.audit_page(
        page_ref,
        markdown,
        auditor,
        run_id = domains.bd.ingestion.ai.params.PAGE_AUDIT_EVALUATION_RUN_ID,
        verifier = finding_verifier,
    )
    return {
        "assessment": audited["assessment"].model_dump(mode = "json"),
        "source_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "status": "complete",
        "warnings": audited["warnings"],
        "audit_mode": audited["audit_mode"],
    }


async def run_ingestion_ai(
    book_id: str,
    *,
    expected_extraction_id: str | None = None,
    agentic_run_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
    auditor: Any | None = None,
    text_repair: Any | None = None,
    structure_repair: Any | None = None,
    metadata_normalizer: Any | None = None,
    content_classifier: Any | None = None,
    contents_reconciler: Any | None = None,
    finding_verifier: Any | None = None,
    checkpointer: Any | None = None,
    resume: bool = False,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Run the full versioned AI workflow against the current immutable Docling extraction.

    A caller that supplies its own auditor owns the whole agent set, so the second-reader verifier is built only for the
    default agents or when passed in."""
    if not _safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_extraction_id is not None and not _safe_segment(expected_extraction_id):
        raise ValueError("expected_extraction_id must be a safe path segment")
    if agentic_run_id is None:
        agentic_run_id = uuid.uuid4().hex
    if not _safe_segment(agentic_run_id):
        raise ValueError("agentic_run_id must be a safe path segment")
    if storage is None:
        storage = infra.minio.service.get_storage()

    domains.settings.chat.service.refresh_endpoint(force = True)

    (
        source_manifest, extraction_id, source_manifest_key, source_manifest_sha256, source_page_refs,
    ) = await _load_run_source(book_id, expected_extraction_id, storage)
    output_manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    existing_manifest = None
    if await storage.exists(output_manifest_key):
        existing_manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest.model_validate_json(
            await storage.read_text(output_manifest_key),
        )
        if existing_manifest.book_id != book_id or existing_manifest.agentic_run_id != agentic_run_id:
            raise ValueError("the stored agentic manifest does not match the requested run")
        if existing_manifest.extraction_run_id != extraction_id:
            raise ValueError("the agentic run is pinned to a different extraction")
        if existing_manifest.source_manifest_sha256 != source_manifest_sha256:
            raise ValueError("the source manifest changed since this agentic run was queued")
        resumable_statuses = {
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED,
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.RUNNING,
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED,
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.CANCELLED,
        }
        if existing_manifest.status == domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE:
            raise ValueError("completed agentic run IDs are immutable")
        if existing_manifest.status not in resumable_statuses:
            raise ValueError("only queued, running, failed, or cancelled agentic runs can be resumed")
        if existing_manifest.status != domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED and not resume:
            raise ValueError("agentic_run_id already exists; set resume=True to continue this run")
        now = existing_manifest.created_at
        manifest_fields = existing_manifest.model_dump(
            exclude = {"status", "updated_at", "finished_at", "error"},
        )
    else:
        if resume:
            raise FileNotFoundError(f"agentic run {agentic_run_id} has no saved manifest to resume")
        now = time.time()
        manifest_fields = _manifest_fields(
            book_id,
            agentic_run_id,
            extraction_id,
            source_manifest_key,
            source_manifest_sha256,
            len(source_page_refs),
            now,
        )

    progress_state = domains.bd.ingestion.ai.schemas.AgenticRunProgress.model_validate(
        manifest_fields.get("progress") or _initial_progress(),
    ).model_dump(mode = "json")
    progress_state["active_audit_pages"] = []
    progress_state["active_repair_pages"] = []
    manifest_fields["progress"] = progress_state
    progress_lock = asyncio.Lock()
    progress_closed = False
    cancellation_observed = False

    async def persist_manifest(status, *, finished_at = None, error = None):
        current_time = max(time.time(), now)
        record = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest(
            **manifest_fields,
            status = status,
            updated_at = current_time,
            finished_at = finished_at,
            error = error,
        )
        await storage.write(
            output_manifest_key,
            json.dumps(record.model_dump(mode = "json"), ensure_ascii = False,
                       separators = (",", ":"), sort_keys = True),
            content_type = "application/json",
        )

    async def report_progress(event: dict[str, Any]) -> None:
        nonlocal progress_state, cancellation_observed
        kind = event.get("kind")
        if cancel_check is not None and event.get("status") == "running":
            if kind == "node" or kind in {"page_audit", "page_repair", "content_classification"}:
                if await cancel_check():
                    cancellation_observed = True
                    raise asyncio.CancelledError
        should_persist = False
        async with progress_lock:
            if progress_closed:
                return
            if kind == "node":
                node = event.get("node")
                status = event.get("status")
                if isinstance(node, str) and status in {
                    "running", "done", "failed",
                }:
                    progress_state["node_status"][node] = status
                    progress_state["current_node"] = node if status == "running" else (
                        None if progress_state.get("current_node") == node else progress_state.get("current_node")
                    )
                    should_persist = not (node == "publish" and status == "done")
            elif kind in {"page_audit", "page_repair", "content_classification"}:
                page = event.get("page")
                status = event.get("status")
                if type(page) is int and status in {"running", "done"}:
                    active_key = {
                        "page_audit": "active_audit_pages",
                        "page_repair": "active_repair_pages",
                        "content_classification": "active_classification_pages",
                    }[kind]
                    completed_key = {
                        "page_audit": "audited_pages",
                        "page_repair": "processed_pages",
                        "content_classification": "classified_pages",
                    }[kind]
                    active_pages = set(progress_state[active_key])
                    if status == "running":
                        active_pages.add(page)
                        if kind == "page_audit":
                            progress_state["audit_warning_pages"] = [
                                current for current in progress_state["audit_warning_pages"]
                                if current != page
                            ]
                    else:
                        active_pages.discard(page)
                        progress_state[completed_key] = sorted(set(progress_state[completed_key]) | {page})
                        if kind == "page_audit":
                            warning_pages = set(progress_state["audit_warning_pages"])
                            if event.get("warning") is True:
                                warning_pages.add(page)
                            progress_state["audit_warning_pages"] = sorted(warning_pages)
                        should_persist = True
                    progress_state[active_key] = sorted(active_pages)
            progress_state = domains.bd.ingestion.ai.schemas.AgenticRunProgress.model_validate(
                progress_state,
            ).model_dump(mode = "json")
            manifest_fields["progress"] = progress_state
            if should_persist:
                try:
                    await persist_manifest(domains.bd.ingestion.ai.schemas.AgenticRunStatus.RUNNING)
                except Exception as error:
                    logger.warning(
                        f"[bd-agentic-progress] manifest update failed for {agentic_run_id}: "
                        f"{type(error).__name__}: {error}"
                    )
            await domains.bd.ingestion.ai.progress.service.emit(
                book_id,
                agentic_run_id,
                kind if isinstance(kind, str) else "progress",
                **{key: value for key, value in event.items() if key != "kind"},
                progress = progress_state,
            )

    if existing_manifest is None:
        await persist_manifest(domains.bd.ingestion.ai.schemas.AgenticRunStatus.QUEUED)
    await persist_manifest(domains.bd.ingestion.ai.schemas.AgenticRunStatus.RUNNING)

    try:
        if auditor is None and finding_verifier is None:
            finding_verifier = ai.bd.agents.ingestion.finding_verifier.agent.finding_verifier()
        if auditor is None:
            auditor = ai.bd.agents.ingestion.page_auditor.agent.page_auditor()
        if text_repair is None:
            text_repair = ai.bd.agents.ingestion.text_repair.agent.text_repair()
        if structure_repair is None:
            structure_repair = ai.bd.agents.ingestion.structure_repair.agent.structure_repair()
        if metadata_normalizer is None:
            metadata_normalizer = ai.bd.agents.ingestion.metadata_normalizer.agent.metadata_normalizer()
        if content_classifier is None:
            content_classifier = ai.bd.agents.ingestion.content_classifier.agent.content_classifier()
        if contents_reconciler is None:
            contents_reconciler = ai.bd.agents.ingestion.contents_reconciler.agent.contents_reconciler()

        async def write_artifact(key: str, content: str, content_type: str) -> None:
            if key == output_manifest_key:
                value = json.loads(content)
                value["progress"] = progress_state
                if value.get("status") == domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE.value:
                    await domains.settings.llm_counter.service.snapshot("bd", agentic_run_id)
                content = json.dumps(
                    value, ensure_ascii = False, separators = (",", ":"), sort_keys = True,
                )
            await storage.write(key, content, content_type = content_type)

        source_metadata = {
            key: source_manifest[key]
            for key in ("filename", "title", "subtitle", "authors", "source_format")
            if key in source_manifest
        }
        graph_state: ai.bd.graphs.ingestion.states.IngestionRunState = {
            "book_id": book_id,
            "extraction_run_id": extraction_id,
            "agentic_run_id": agentic_run_id,
            "workflow_version": domains.bd.ingestion.ai.versions.WORKFLOW_VERSION,
            "source_manifest_key": source_manifest_key,
            "source_manifest_sha256": source_manifest_sha256,
            "run_created_at": now,
            "expected_page_count": len(source_page_refs),
            "source_page_refs": source_page_refs,
            "source_outline": source_manifest.get("outline", []) if isinstance(source_manifest.get("outline"), list) else [],
            "page_refs": [],
            "page_audits": [],
            "work_items": [],
            "page_results": [],
            "source_metadata": source_metadata,
            "status": "running",
        }
        graph = ai.bd.graphs.ingestion.graphs.ingestion_graph(checkpointer = checkpointer)
        dependencies = ai.bd.graphs.ingestion.entities.IngestionDependencies(
            read_page = storage.read_text,
            write_artifact = write_artifact,
            artifact_exists = storage.exists,
            auditor = auditor,
            specialists = {"text_repair": text_repair, "structure": structure_repair},
            metadata_normalizer = metadata_normalizer,
            content_classifier = content_classifier,
            contents_reconciler = contents_reconciler,
            progress_callback = report_progress,
            finding_verifier = finding_verifier,
        )
        config = {"configurable": {
            "thread_id": f"bd-ingestion-agentic:{book_id}:{extraction_id}:{agentic_run_id}",
        }}
        if checkpointer is None:
            result = await graph.ainvoke(graph_state, context = dependencies)
        else:
            snapshot = await graph.aget_state(config)
            graph_input = None if snapshot.next else graph_state
            result = await graph.ainvoke(graph_input, config = config, context = dependencies)
        final_manifest = json.loads(await storage.read_text(output_manifest_key))
        final_manifest["progress"] = domains.bd.ingestion.ai.schemas.AgenticRunProgress.model_validate(
            progress_state,
        ).model_dump(mode = "json")
        await storage.write(
            output_manifest_key,
            json.dumps(final_manifest, ensure_ascii = False, separators = (",", ":"), sort_keys = True),
            content_type = "application/json",
        )
        await domains.bd.ingestion.ai.progress.service.emit(
            book_id,
            agentic_run_id,
            "run_done",
            status = domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE.value,
            progress = progress_state,
        )
        return result
    except (asyncio.CancelledError, NodeCancelledError) as error:
        # LangGraph 1.2 wraps CancelledError raised by a node in NodeCancelledError.
        # Only treat that wrapper as a cooperative run cancellation when our Redis
        # callback actually observed the run's cancel flag.
        if isinstance(error, NodeCancelledError) and not cancellation_observed:
            raise
        cancelled_at = max(time.time(), now)
        async with progress_lock:
            progress_closed = True
            progress_state["active_audit_pages"] = []
            progress_state["active_repair_pages"] = []
            progress_state["active_classification_pages"] = []
            progress_state["current_node"] = None
            progress_state["node_status"] = {
                node: (
                    domains.bd.ingestion.ai.schemas.AgenticNodeStatus.PENDING.value
                    if status == domains.bd.ingestion.ai.schemas.AgenticNodeStatus.RUNNING.value
                    else status
                )
                for node, status in progress_state["node_status"].items()
            }
            manifest_fields["progress"] = progress_state
        await domains.settings.llm_counter.service.snapshot("bd", agentic_run_id)
        await persist_manifest(
            domains.bd.ingestion.ai.schemas.AgenticRunStatus.CANCELLED,
            finished_at = cancelled_at,
        )
        await domains.bd.ingestion.ai.progress.service.emit(
            book_id,
            agentic_run_id,
            "run_cancelled",
            status = domains.bd.ingestion.ai.schemas.AgenticRunStatus.CANCELLED.value,
            progress = progress_state,
        )
        return {
            "book_id": book_id,
            "extraction_run_id": extraction_id,
            "agentic_run_id": agentic_run_id,
            "status": domains.bd.ingestion.ai.schemas.AgenticRunStatus.CANCELLED.value,
            "progress": progress_state,
        }
    except Exception as error:
        failed_at = max(time.time(), now)
        async with progress_lock:
            progress_closed = True
            progress_state["active_audit_pages"] = []
            progress_state["active_repair_pages"] = []
            current_node = progress_state.get("current_node")
            if not current_node:
                current_node = next(
                    (
                        node for node in domains.bd.ingestion.ai.params.GRAPH_NODES
                        if progress_state.get("node_status", {}).get(node)
                        != domains.bd.ingestion.ai.schemas.AgenticNodeStatus.DONE.value
                    ),
                    None,
                )
            if current_node in progress_state.get("node_status", {}):
                progress_state["node_status"][current_node] = domains.bd.ingestion.ai.schemas.AgenticNodeStatus.FAILED.value
            progress_state["current_node"] = current_node
            manifest_fields["progress"] = progress_state
        error_message = _safe_error_message(error)
        try:
            await domains.settings.llm_counter.service.snapshot("bd", agentic_run_id)
            await persist_manifest(
                domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED,
                finished_at = failed_at,
                error = error_message,
            )
            await domains.bd.ingestion.ai.progress.service.emit(
                book_id,
                agentic_run_id,
                "run_failed",
                status = domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED.value,
                error = error_message,
                progress = progress_state,
            )
        except Exception:
            pass
        raise
