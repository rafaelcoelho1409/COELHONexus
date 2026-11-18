"""Run integration between completed Book Distiller extractions and the AI graph."""
from __future__ import annotations
import ai, infra
from . import (
    content_selection,
    params,
    processed_book,
    progress,
    runs,
    schemas,
    source_pages,
    versions,
)
from .. import source
from .... import settings

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from langgraph.errors import NodeCancelledError


logger = logging.getLogger(__name__)


class _InterruptedRunError(RuntimeError):
    """An Agentic AI worker stopped while its persisted run was still active."""


def _is_missing_storage_object(error: Exception) -> bool:
    if isinstance(error, FileNotFoundError):
        return True
    if not isinstance(error, infra.minio.service.ClientError):
        return False
    code = (error.response or {}).get("Error", {}).get("Code", "")
    return code in {"404", "NoSuchKey"}


async def _load_run_source(
    book_id: str,
    expected_extraction_id: str | None,
    storage: infra.minio.service.MinIOStorage,
) -> tuple[dict, str, str, str, list[dict]]:
    source_manifest = await source.storage.service.read_manifest(storage, book_id)
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
    source_manifest_key = source.storage.keys.manifest_key(book_id)
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
        "workflow_version": versions.WORKFLOW_VERSION,
        "agentic_run_id": agentic_run_id,
        "book_id": book_id,
        "extraction_run_id": extraction_id,
        "source_manifest_key": source_manifest_key,
        "source_manifest_sha256": source_manifest_sha256,
        "expected_page_count": expected_page_count,
        "progress": _initial_progress(),
        "pages": [],
        "book_metadata_key": source.storage.keys.agentic_book_metadata_key(
            book_id, agentic_run_id,
        ),
        "quality_report_key": source.storage.keys.agentic_quality_report_key(
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
    key = source.storage.keys.current_agentic_run_key(book_id)
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
            node: runs.schemas.AgenticNodeStatus.PENDING.value
            for node in params.GRAPH_NODES
        },
        "audited_pages": [],
        "audit_warning_pages": [],
        "processed_pages": [],
        "repaired_pages": [],
        "active_audit_pages": [],
        "active_repair_pages": [],
        "classified_pages": [],
        "active_classification_pages": [],
        "node_page_progress": {},
        "selected_page_count": 0,
        "excluded_page_count": 0,
    }


def _clear_node_page_activity(progress: dict[str, Any]) -> None:
    node_progress = progress.get("node_page_progress")
    if not isinstance(node_progress, dict):
        return
    for item in node_progress.values():
        if isinstance(item, dict):
            item["active_count"] = 0


def _safe_error_message(error: Exception | str) -> str:
    current = error
    seen: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if isinstance(current, _InterruptedRunError):
            return str(current)
        if isinstance(current, ValueError) and "http:// or https://" in message:
            return "The configured language-model endpoint URL is invalid. Add an http:// or https:// scheme in Settings, then retry."
        if isinstance(current, ValueError) and "older ingestion graph checkpoint" in message:
            return str(current)
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
    if not schemas.is_safe_segment(extraction_run_id):
        raise ValueError("extraction_run_id must be a safe path segment")
    return f"{params.AUTO_RUN_ID_PREFIX}{extraction_run_id}"


async def create_ingestion_ai_run(
    book_id: str,
    *,
    expected_extraction_id: str | None = None,
    agentic_run_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    if not schemas.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            progress.keys.book_mutation_lock_key(book_id),
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
    if not schemas.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_extraction_id is not None and not schemas.is_safe_segment(expected_extraction_id):
        raise ValueError("expected_extraction_id must be a safe path segment")
    if agentic_run_id is None:
        agentic_run_id = uuid.uuid4().hex
    if not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("agentic_run_id must be a safe path segment")
    if storage is None:
        storage = infra.minio.service.get_storage()

    (
        _, extraction_id, source_manifest_key, source_manifest_sha256, source_page_refs,
    ) = await _load_run_source(book_id, expected_extraction_id, storage)
    active_run = await find_active_ingestion_ai_run(book_id, storage = storage)
    if active_run is not None and active_run["agentic_run_id"] != agentic_run_id:
        raise ValueError("another Agentic AI run is already active for this book")
    manifest_key = source.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    if await storage.exists(manifest_key):
        raise ValueError("agentic_run_id already exists; use a new ID to preserve immutable run history")
    await progress.service.clear_cancel(book_id, agentic_run_id)

    now = time.time()
    manifest = runs.schemas.IngestionAIRunManifest(
        **_manifest_fields(
            book_id,
            agentic_run_id,
            extraction_id,
            source_manifest_key,
            source_manifest_sha256,
            len(source_page_refs),
            now,
        ),
        status = runs.schemas.AgenticRunStatus.QUEUED,
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
    source_manifest = await source.storage.service.read_manifest(storage, book_id)
    if source_manifest is None or source_manifest.get("status") != source.storage.params.STATUS_DONE:
        return None
    extraction_run_id = source_manifest.get("extraction_id")
    if not schemas.is_safe_segment(extraction_run_id):
        return None
    pointer_key = source.storage.keys.current_agentic_run_key(book_id)
    if await storage.exists(pointer_key):
        pointer = source.storage.domain.parse_json(
            await storage.read_text(pointer_key),
            {},
        )
        if isinstance(pointer, dict) and pointer.get("agentic_run_id") is None:
            return None
        run_id = pointer.get("agentic_run_id") if isinstance(pointer, dict) else None
        if schemas.is_safe_segment(run_id):
            current = await read_ingestion_ai_run(book_id, run_id, storage = storage)
            if current is not None and current.get("extraction_run_id") == extraction_run_id:
                return current
    run_ids = await storage.list_subfolders(
        source.storage.keys.agentic_runs_prefix(book_id),
    )
    runs = await asyncio.gather(*(
        read_ingestion_ai_run(book_id, run_id, storage = storage)
        for run_id in run_ids
        if schemas.is_safe_segment(run_id)
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
    except (FileNotFoundError, infra.minio.service.ClientError) as error:
        if not _is_missing_storage_object(error):
            raise
        # Node replay removes downstream metadata while keeping the book active.
        # Treat that transiently absent artifact as unavailable so the book
        # snapshot can fall back to the source manifest title.
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
    page_source: str | None = None,
) -> tuple[str | None, str, list[str]]:
    """Return a completed automatic AI page when available, otherwise the immutable Docling page.

    `page_source` pins one side for comparison: "docling" always reads the raw page; "agentic" reads the AI page or nothing."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    if page_source != "docling":
        run = await read_current_ingestion_ai_run(book_id, storage = storage)
        if run is not None and page_number in run["progress"]["processed_pages"]:
            artifact = next((item for item in run["pages"] if item["page_number"] == page_number), None)
            normalized_key = artifact.get("enriched_key") if artifact is not None else None
            visual_key = source.storage.keys.agentic_visual_page_key(
                book_id, run["agentic_run_id"], page_number,
            )
            try:
                if normalized_key is None and await storage.exists(visual_key):
                    markdown = await storage.read_text(visual_key)
                    artifacts_key = source.storage.keys.agentic_visual_artifacts_key(
                        book_id, run["agentic_run_id"], page_number,
                    )
                    converted = []
                    if await storage.exists(artifacts_key):
                        artifacts = json.loads(await storage.read_text(artifacts_key))
                        converted = [
                            item["figure_name"] for item in artifacts.get("conversions", [])
                            if isinstance(item, dict) and item.get("converted") is True
                            and isinstance(item.get("figure_name"), str)
                        ]
                    return markdown, "agentic", converted
                if normalized_key is None:
                    normalized_key = (
                        artifact["normalized_key"]
                        if artifact is not None else source.storage.keys.agentic_page_key(
                            book_id, run["agentic_run_id"], page_number,
                        )
                    )
                converted = artifact.get("converted_figures", []) if artifact is not None else []
                markdown = await storage.read_text(normalized_key)
                expected_sha256 = (
                    artifact.get("enriched_sha256") or artifact.get("normalized_sha256")
                    if artifact is not None else None
                )
                if expected_sha256 and hashlib.sha256(markdown.encode("utf-8")).hexdigest() != expected_sha256:
                    if page_source == "agentic":
                        return None, "agentic", []
                return markdown, "agentic", converted
            except FileNotFoundError:
                pass
        if page_source == "agentic":
            return None, "agentic", []
    markdown = await source.storage.service.read_page(storage, book_id, page_number)
    return markdown, "docling", []


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
    except (FileNotFoundError, infra.minio.service.ClientError) as error:
        if not _is_missing_storage_object(error):
            raise
        # Replay may have removed the old result artifacts while this page's
        # replacement result is still being generated.
        return None
    auto_applied = set(proposals.get("auto_applied_issue_ids", []))
    review_required = set(proposals.get("review_required_issue_ids", []))
    proposal_by_issue = {proposal["issue_id"]: proposal for proposal in proposals.get("proposals", [])}
    verification_by_issue = {
        result["issue_id"]: result
        for result in proposals.get("source_verification_results", [])
        if isinstance(result, dict) and isinstance(result.get("issue_id"), str)
    }

    def proposal_details(issue_id: str) -> dict[str, Any] | None:
        proposal = proposal_by_issue.get(issue_id)
        if proposal is None:
            return None
        verification = verification_by_issue.get(issue_id)
        verification_status = verification.get("status") if verification else None
        verification_reason = verification.get("reason") if verification else None
        if issue_id in auto_applied and verification_status not in {None, "confirmed"}:
            verification_status = "advisory"
            verification_reason = (
                "Image verification is advisory; the validated quality correction was applied automatically."
            )
        return {
            "target_text": proposal["target_text"],
            "replacement": proposal["replacement"],
            "rationale": proposal["rationale"],
            "confidence": proposal["confidence"],
            "risk": proposal["risk"],
            "verification_status": verification_status,
            "source_verification_status": (
                verification.get("status") if verification else None
            ),
            "verification_reason": verification_reason,
        }

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
                "proposal": proposal_details(issue["issue_id"]),
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
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    manifest_key = source.storage.keys.agentic_manifest_key(book_id, agentic_run_id)
    if not await storage.exists(manifest_key):
        return None
    manifest = runs.schemas.IngestionAIRunManifest.model_validate_json(
        await storage.read_text(manifest_key),
    )
    if manifest.book_id != book_id or manifest.agentic_run_id != agentic_run_id:
        raise ValueError("the stored agentic manifest does not match the requested run")
    return manifest.model_dump(mode = "json")


async def _ensure_ingestion_ai_worker_stopped(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
) -> None:
    if await progress.service.run_execution_lock_is_held(
        redis,
        book_id,
        agentic_run_id,
    ):
        raise ValueError("the Agentic AI worker is still exiting; wait for it to stop before retrying this run")


async def _recover_stale_running_ingestion_ai_run(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
    manifest: dict[str, Any],
    *,
    storage: infra.minio.service.MinIOStorage,
) -> dict[str, Any]:
    """Mark an abandoned run failed only after its worker lease and progress have both gone stale."""
    if manifest.get("status") != runs.schemas.AgenticRunStatus.RUNNING.value:
        return manifest
    await _ensure_ingestion_ai_worker_stopped(redis, book_id, agentic_run_id)
    updated_at = manifest.get("updated_at")
    stale_after = progress.params.RUN_EXECUTION_LOCK_STALE_AFTER_S
    if type(updated_at) not in {int, float} or time.time() - updated_at < stale_after:
        raise ValueError("the Agentic AI run is still active; wait for the current graph node to finish")

    await mark_ingestion_ai_run_failed(
        book_id,
        agentic_run_id,
        _InterruptedRunError(
            "The worker stopped before the run finished. Resume to continue from its saved checkpoint."
        ),
        storage = storage,
    )
    recovered = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if recovered is None:
        raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
    if recovered["status"] == runs.schemas.AgenticRunStatus.FAILED.value:
        await progress.service.emit(
            book_id,
            agentic_run_id,
            "run_failed",
            status = recovered["status"],
            error = recovered["error"],
            progress = recovered["progress"],
        )
    return recovered


async def read_selection(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[content_selection.schemas.ContentSelectionManifest, str, str] | None:
    """Return the current page-selection revision, key, and exact content hash."""
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    pointer_key = source.storage.keys.agentic_current_content_selection_key(
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
    expected_key = source.storage.keys.agentic_content_selection_key(
        book_id, agentic_run_id, revision,
    )
    if key != expected_key:
        raise ValueError("the current content-selection pointer does not match its revision")
    content = await storage.read_text(key)
    actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if actual_digest != digest:
        raise ValueError("the current content-selection revision failed its integrity check")
    manifest = content_selection.schemas.ContentSelectionManifest.model_validate_json(content)
    if manifest.book_id != book_id or manifest.agentic_run_id != agentic_run_id:
        raise ValueError("the current content selection belongs to a different run")
    return manifest, key, actual_digest


async def ensure_selection(
    book_id: str,
    agentic_run_id: str,
    *,
    ingestion_ai_run: dict[str, Any] | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[content_selection.schemas.ContentSelectionManifest, str, str]:
    """Return a selection, migrating completed pre-classifier runs with a conservative default.

    Older completed runs have no content-selection artifact. Keep known blank pages excluded and
    retain every other page as unknown so a compatibility migration can never silently drop book
    content. The generated artifact is immutable and reviewed edits continue through revisions.
    """
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()

    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        current = await read_selection(book_id, agentic_run_id, storage = storage)
        if current is not None:
            return current

        manifest = ingestion_ai_run or await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError(f"Agentic ingestion run {agentic_run_id!r} was not found")
        if manifest.get("book_id") != book_id or manifest.get("agentic_run_id") != agentic_run_id:
            raise ValueError("the ingestion run does not match the requested content selection")
        if manifest.get("status") != runs.schemas.AgenticRunStatus.DONE.value:
            raise ValueError("content selection is available after ingestion completes")

        revision = 1
        while True:
            key = source.storage.keys.agentic_content_selection_key(
                book_id, agentic_run_id, revision,
            )
            if not await storage.exists(key):
                selection = content_selection.domain.build_legacy_selection(manifest, revision, time.time())
                content = content_selection.domain.selection_json(selection)
                await storage.write(key, content, content_type = "application/json")
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                break

            content = await storage.read_text(key)
            try:
                existing = content_selection.schemas.ContentSelectionManifest.model_validate_json(content)
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

        pointer_key = source.storage.keys.agentic_current_content_selection_key(
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
) -> content_selection.schemas.ContentSelectionManifest:
    """Append a reviewed selection revision and only then move its current pointer."""
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            progress.keys.book_mutation_lock_key(book_id),
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
) -> content_selection.schemas.ContentSelectionManifest:
    current = await read_selection(book_id, agentic_run_id, storage = storage)
    if current is None:
        raise FileNotFoundError("the ingestion run has no content selection")
    manifest, _, _ = current
    updated = content_selection.domain.apply_selection_overrides(
        manifest,
        overrides,
        expected_revision = expected_revision,
        revision = manifest.revision + 1,
        created_at = time.time(),
    )
    if updated is manifest:
        return manifest

    revision = updated.revision
    key = source.storage.keys.agentic_content_selection_key(
        book_id, agentic_run_id, revision,
    )
    # A crash after writing the immutable revision but before moving the
    # current pointer can leave an unreferenced object. Skip it on retry.
    while await storage.exists(key):
        revision += 1
        key = source.storage.keys.agentic_content_selection_key(
            book_id, agentic_run_id, revision,
        )
    if revision != updated.revision:
        updated = updated.model_copy(update = {"revision": revision})

    ingestion_ai_run = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if ingestion_ai_run is None:
        raise FileNotFoundError(f"Agentic ingestion run {agentic_run_id!r} was not found")

    content = content_selection.domain.selection_json(updated)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    await storage.write(key, content, content_type = "application/json")
    processed, processed_key, processed_sha256 = await processed_book.service.ensure_for_selection(
        book_id,
        agentic_run_id,
        updated,
        key,
        digest,
        ingestion_ai_run = ingestion_ai_run,
        storage = storage,
    )
    pointer_key = source.storage.keys.agentic_current_content_selection_key(
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
    await processed_book.service.write_current_pointer(
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
        source.storage.keys.agentic_runs_prefix(book_id),
    )
    for run_id in run_ids:
        if not schemas.is_safe_segment(run_id) or run_id == exclude_run_id:
            continue
        manifest = await read_ingestion_ai_run(book_id, run_id, storage = storage)
        if manifest is not None and manifest["status"] in {
            runs.schemas.AgenticRunStatus.QUEUED.value,
            runs.schemas.AgenticRunStatus.RUNNING.value,
        }:
            return manifest
    return None


async def cancel_ingestion_ai_run(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    """Request a cooperative stop at a page, node, or cancellable LLM-call boundary."""
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
        redis.lock(
            progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_ingestion_ai_run(book_id, agentic_run_id)
        if manifest is None:
            raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
        if manifest["status"] not in {
            runs.schemas.AgenticRunStatus.QUEUED.value,
            runs.schemas.AgenticRunStatus.RUNNING.value,
        }:
            return {
                "book_id": book_id,
                "agentic_run_id": agentic_run_id,
                "status": manifest["status"],
            }
        await progress.service.request_cancel(book_id, agentic_run_id)
        await progress.service.emit(
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
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            progress.keys.book_mutation_lock_key(book_id),
            timeout = 3600,
            blocking_timeout = 30,
        ),
        redis.lock(
            progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
            timeout = 3600,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
        await _ensure_ingestion_ai_worker_stopped(redis, book_id, agentic_run_id)
        if manifest["status"] in {
            runs.schemas.AgenticRunStatus.QUEUED.value,
            runs.schemas.AgenticRunStatus.RUNNING.value,
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
            source.storage.keys.agentic_prefix(book_id, agentic_run_id),
        )
        await settings.llm_counter.service.delete_run("bd", agentic_run_id)
        await redis.delete(
            progress.keys.cancel_key(book_id, agentic_run_id),
            progress.keys.snapshot_key(book_id, agentic_run_id),
        )
        pointer_key = source.storage.keys.current_agentic_run_key(book_id)
        if await storage.exists(pointer_key):
            pointer = source.storage.domain.parse_json(
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
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
        redis.lock(
            progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        return await _queue_ingestion_ai_resume_unlocked(
            redis,
            book_id,
            agentic_run_id,
            storage = storage,
        )


async def _queue_ingestion_ai_resume_unlocked(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    """Requeue a failed candidate with the same source identity and graph thread."""
    manifest = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if manifest is None:
        raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
    if storage is None:
        storage = infra.minio.service.get_storage()
    manifest = await _recover_stale_running_ingestion_ai_run(
        redis,
        book_id,
        agentic_run_id,
        manifest,
        storage = storage,
    )
    if manifest["status"] not in {
        runs.schemas.AgenticRunStatus.FAILED.value,
        runs.schemas.AgenticRunStatus.CANCELLED.value,
    }:
        raise ValueError("only failed or cancelled agentic runs can be resumed")
    if manifest.get("workflow_version") != versions.WORKFLOW_VERSION:
        raise ValueError(
            "This run uses an older ingestion graph. Start a fresh Agentic AI run to use the current graph."
        )
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
    await progress.service.clear_cancel(book_id, agentic_run_id)
    await progress.service.clear_events(book_id, agentic_run_id)
    manifest["status"] = runs.schemas.AgenticRunStatus.QUEUED.value
    manifest["updated_at"] = max(time.time(), manifest["created_at"])
    manifest["finished_at"] = None
    manifest["error"] = None
    manifest_key = source.storage.keys.agentic_manifest_key(
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


async def queue_ingestion_ai_node_replay(
    book_id: str,
    agentic_run_id: str,
    node_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    """Queue a terminal run from a saved checkpoint immediately before one graph node."""
    if not schemas.is_safe_segment(book_id) or not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if node_id not in params.GRAPH_NODES:
        raise ValueError(f"unknown ingestion graph node {node_id!r}")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
        redis.lock(
            progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
        if storage is None:
            storage = infra.minio.service.get_storage()
        manifest = await _recover_stale_running_ingestion_ai_run(
            redis,
            book_id,
            agentic_run_id,
            manifest,
            storage = storage,
        )
        if manifest["status"] not in {
            runs.schemas.AgenticRunStatus.DONE.value,
            runs.schemas.AgenticRunStatus.FAILED.value,
            runs.schemas.AgenticRunStatus.CANCELLED.value,
        }:
            raise ValueError("stop the active Agentic AI run before replaying a node")
        if manifest.get("workflow_version") != versions.WORKFLOW_VERSION:
            raise ValueError(
                "This run uses an older ingestion graph. Start a fresh Agentic AI run to use the current graph."
            )
        active_run = await find_active_ingestion_ai_run(
            book_id, storage = storage, exclude_run_id = agentic_run_id,
        )
        if active_run is not None:
            raise ValueError("stop the other active Agentic AI run before replaying a node")
        _, extraction_id, _, source_manifest_sha256, _ = await _load_run_source(
            book_id, manifest["extraction_run_id"], storage,
        )
        if extraction_id != manifest["extraction_run_id"] or source_manifest_sha256 != manifest["source_manifest_sha256"]:
            raise ValueError("the agentic run no longer matches the current extraction")

        graph = ai.bd.graphs.ingestion.graphs.ingestion_graph(
            checkpointer = infra.postgres.service.get_checkpointer(),
        )
        graph_config = {"configurable": {
            "thread_id": f"bd-ingestion-agentic:{book_id}:{extraction_id}:{agentic_run_id}",
        }}
        latest_snapshot = await graph.aget_state(graph_config)
        replay_state = latest_snapshot.values if isinstance(latest_snapshot.values, dict) else {}
        if node_id == "reconcile_contents":
            candidate_key = replay_state.get("processed_book_candidate_key")
            candidate_revision = replay_state.get("processed_book_revision")
            if (
                not isinstance(candidate_key, str)
                or type(candidate_revision) is not int
                or candidate_revision < 1
                or not await storage.exists(candidate_key)
            ):
                logger.warning(
                    "[bd-agentic] candidate artifact missing for contents replay; rewinding to materialization "
                    "book_id=%s run_id=%s candidate_key=%s",
                    book_id,
                    agentic_run_id,
                    candidate_key,
                )
                node_id = "materialize_processed_book"

        node_status = (manifest.get("progress") or {}).get("node_status", {})
        incomplete_predecessors = [
            predecessor
            for predecessor in params.REPLAY_PREREQUISITES[node_id]
            if node_status.get(predecessor)
            != runs.schemas.AgenticNodeStatus.DONE.value
        ]
        if incomplete_predecessors:
            predecessor = incomplete_predecessors[0]
            raise ValueError(
                f"Cannot replay {node_id} yet: {predecessor} has not completed. "
                f"Resume the run from its saved checkpoint or replay {predecessor}."
            )
        await _checkpoint_before_ingestion_node(graph, graph_config, node_id)

        if node_id == "reconcile_contents":
            await processed_book.service.clear_manifest_for_replay(
                book_id,
                agentic_run_id,
                replay_state["processed_book_revision"],
                storage,
            )

        descendants = set(params.REPLAY_DESCENDANTS[node_id])
        rerun_nodes = set(descendants)
        if node_id in {"classify_content", "normalize_metadata"}:
            branch_nodes = {"classify_content", "plan_repairs", "repair_pages", "normalize_metadata"}
            rerun_nodes.update(
                branch_node
                for branch_node in branch_nodes - descendants
                if node_status.get(branch_node) != runs.schemas.AgenticNodeStatus.DONE.value
            )
        progress_state = manifest.get("progress") or _initial_progress()
        for node in descendants:
            progress_state.setdefault("node_status", {})[node] = (
                runs.schemas.AgenticNodeStatus.PENDING.value
            )
        progress_state["current_node"] = None
        if "audit_batch" in descendants:
            progress_state["audited_pages"] = []
            progress_state["audit_warning_pages"] = []
        if "classify_content" in descendants:
            progress_state["classified_pages"] = []
            progress_state["selected_page_count"] = 0
            progress_state["excluded_page_count"] = 0
        if "repair_pages" in descendants:
            progress_state["processed_pages"] = []
            progress_state["repaired_pages"] = []
        progress_state["active_audit_pages"] = []
        progress_state["active_repair_pages"] = []
        progress_state["active_classification_pages"] = []
        if "enrich_visuals" in descendants:
            progress_state["visual_candidate_count"] = 0
            progress_state["visual_completed_count"] = 0
            progress_state["visual_converted_count"] = 0
            progress_state["visual_failed_count"] = 0
        node_page_progress = progress_state.setdefault("node_page_progress", {})
        if isinstance(node_page_progress, dict):
            for node in {node_id, *descendants}:
                node_page_progress.pop(node, None)
        manifest["progress"] = progress_state
        manifest["status"] = runs.schemas.AgenticRunStatus.QUEUED.value
        manifest["updated_at"] = max(time.time(), manifest["created_at"])
        manifest["finished_at"] = None
        manifest["error"] = None
        manifest["replay_from_node"] = node_id

        prefix = source.storage.keys.agentic_prefix(book_id, agentic_run_id)
        artifacts_by_node = {
            "audit_batch": ("pages/", "assessments/", "patch-proposals/", "book-metadata.json", "quality-report.json", "content-selection/", "visual-enrichment/pages/", "processed-book/"),
            "classify_content": ("pages/", "patch-proposals/", "quality-report.json", "content-selection/", "visual-enrichment/pages/", "processed-book/"),
            "plan_repairs": ("pages/", "patch-proposals/", "quality-report.json", "processed-book/", "visual-enrichment/pages/"),
            "repair_pages": ("pages/", "patch-proposals/", "quality-report.json", "processed-book/", "visual-enrichment/pages/"),
            "normalize_metadata": ("book-metadata.json", "quality-report.json", "processed-book/", "visual-enrichment/pages/"),
            # Keep validated per-figure responses for reuse; the input fingerprint decides if changed context requires a fresh call.
            "enrich_visuals": ("visual-enrichment/pages/", "processed-book/"),
            "materialize_processed_book": ("processed-book/",),
            "reconcile_contents": (),
            "publish": ("quality-report.json",),
        }
        artifact_paths = {
            path
            for node in descendants
            for path in artifacts_by_node[node]
        }
        for path in sorted(artifact_paths):
            await storage.delete_prefix(prefix + path)

        retained_counter_nodes = {
            counter_node
            for graph_node, counter_nodes in (
                params.REPLAY_LLM_COUNTER_NODES_BY_GRAPH_NODE.items()
            )
            if graph_node not in rerun_nodes
            for counter_node in counter_nodes
        }
        await settings.llm_counter.service.retain_nodes("bd", agentic_run_id, retained_counter_nodes)

        manifest_key = source.storage.keys.agentic_manifest_key(book_id, agentic_run_id)
        await storage.write(
            manifest_key,
            json.dumps(manifest, ensure_ascii = False, separators = (",", ":"), sort_keys = True),
            content_type = "application/json",
        )
        await _write_current_run_pointer(storage, book_id, agentic_run_id, extraction_id)
        await progress.service.clear_cancel(book_id, agentic_run_id)
        await progress.service.clear_events(book_id, agentic_run_id)
        return manifest


async def _checkpoint_before_ingestion_node(
    graph: Any,
    config: dict[str, Any],
    node_id: str,
) -> tuple[dict[str, Any], str | None]:
    """Find a replay point and the parent to resume when the selected node shares a fork checkpoint."""
    fork_nodes = {"classify_content", "normalize_metadata"}
    async for snapshot in graph.aget_state_history(config):
        next_nodes = set(snapshot.next or ())
        if node_id not in next_nodes:
            continue
        if next_nodes == {node_id}:
            return snapshot.config, None
        if node_id in fork_nodes and next_nodes == fork_nodes:
            return snapshot.config, "audit_batch"
        raise ValueError(
            f"node {node_id!r} shares a checkpoint with unrelated pending nodes and cannot be replayed independently"
        )
    raise ValueError(f"no saved checkpoint schedules ingestion node {node_id!r}")


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
        runs.schemas.AgenticRunStatus.DONE.value,
        runs.schemas.AgenticRunStatus.FAILED.value,
        runs.schemas.AgenticRunStatus.CANCELLED.value,
    }:
        return
    if storage is None:
        storage = infra.minio.service.get_storage()
    finished_at = max(time.time(), manifest["created_at"])
    error_text = _safe_error_message(error)
    manifest["status"] = runs.schemas.AgenticRunStatus.FAILED.value
    manifest["updated_at"] = finished_at
    manifest["finished_at"] = finished_at
    manifest["error"] = error_text[:500]
    progress_state = manifest.get("progress") or _initial_progress()
    progress_state["active_audit_pages"] = []
    progress_state["active_repair_pages"] = []
    progress_state["active_classification_pages"] = []
    _clear_node_page_activity(progress_state)
    current_node = progress_state.get("current_node")
    if not current_node:
        current_node = next(
            (
                node for node in params.GRAPH_NODES
                if progress_state.get("node_status", {}).get(node) != runs.schemas.AgenticNodeStatus.DONE.value
            ),
            None,
        )
    if current_node in progress_state.get("node_status", {}):
        progress_state["node_status"][current_node] = runs.schemas.AgenticNodeStatus.FAILED.value
    progress_state["current_node"] = current_node
    manifest["progress"] = progress_state
    manifest_key = source.storage.keys.agentic_manifest_key(
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
) -> tuple[str, schemas.ExtractionRequest]:
    if manifest.get("book_id") != book_id:
        raise ValueError("the manifest book_id does not match the requested book")

    extraction_id = manifest.get("extraction_id")
    if not schemas.is_safe_segment(extraction_id):
        raise ValueError("the manifest has no safe extraction_id")
    if expected_extraction_id is not None and extraction_id != expected_extraction_id:
        raise ValueError("the requested extraction_id is no longer current")
    if manifest.get("status") != source.storage.params.STATUS_DONE:
        raise ValueError("only completed extraction runs can be inventoried")

    page_count = manifest.get("n_pages")
    pages = manifest.get("pages")
    if type(page_count) is not int or page_count <= 0:
        raise ValueError("the manifest must contain a positive n_pages value")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise ValueError("the completed manifest must contain every extracted page")
    if any(not isinstance(page, dict) for page in pages):
        raise ValueError("every manifest page entry must be an object")

    request = schemas.ExtractionRequest(
        book_id = book_id,
        extraction_run_id = extraction_id,
        extraction_status = manifest["status"],
        expected_page_count = page_count,
        page_refs = [
            {
                "page": page.get("page"),
                "key": page.get("key"),
                "pictures": page.get("pictures", 0),
                "figures": page.get("figures", []),
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
    if not schemas.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_extraction_id is not None and not schemas.is_safe_segment(expected_extraction_id):
        raise ValueError("expected_extraction_id must be a safe path segment")
    if type(page_number) is not int or page_number <= 0:
        raise ValueError("page_number must be a positive integer")
    if storage is None:
        storage = infra.minio.service.get_storage()

    manifest = await source.storage.service.read_manifest(storage, book_id)
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
        run_id = params.PAGE_AUDIT_EVALUATION_RUN_ID,
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
    finding_verifier: Any | None = None,
    patch_verifier: Any | None = None,
    checkpointer: Any | None = None,
    resume: bool = False,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    execution_check: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run the full versioned AI workflow against the current immutable Docling extraction.

    A caller that supplies its own auditor owns the whole agent set, so the second-reader verifier is built only for the
    default agents or when passed in."""
    if not schemas.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_extraction_id is not None and not schemas.is_safe_segment(expected_extraction_id):
        raise ValueError("expected_extraction_id must be a safe path segment")
    if agentic_run_id is None:
        agentic_run_id = uuid.uuid4().hex
    if not schemas.is_safe_segment(agentic_run_id):
        raise ValueError("agentic_run_id must be a safe path segment")
    if storage is None:
        storage = infra.minio.service.get_storage()

    settings.chat.service.refresh_endpoint(force = True)

    (
        source_manifest, extraction_id, source_manifest_key, source_manifest_sha256, source_page_refs,
    ) = await _load_run_source(book_id, expected_extraction_id, storage)
    output_manifest_key = source.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    existing_manifest = None
    if await storage.exists(output_manifest_key):
        existing_manifest = runs.schemas.IngestionAIRunManifest.model_validate_json(
            await storage.read_text(output_manifest_key),
        )
        if existing_manifest.book_id != book_id or existing_manifest.agentic_run_id != agentic_run_id:
            raise ValueError("the stored agentic manifest does not match the requested run")
        if existing_manifest.extraction_run_id != extraction_id:
            raise ValueError("the agentic run is pinned to a different extraction")
        if existing_manifest.source_manifest_sha256 != source_manifest_sha256:
            raise ValueError("the source manifest changed since this agentic run was queued")
        resumable_statuses = {
            runs.schemas.AgenticRunStatus.QUEUED,
            runs.schemas.AgenticRunStatus.RUNNING,
            runs.schemas.AgenticRunStatus.FAILED,
            runs.schemas.AgenticRunStatus.CANCELLED,
        }
        if existing_manifest.status == runs.schemas.AgenticRunStatus.DONE:
            raise ValueError("completed agentic run IDs are immutable")
        if existing_manifest.status not in resumable_statuses:
            raise ValueError("only queued, running, failed, or cancelled agentic runs can be resumed")
        if existing_manifest.status != runs.schemas.AgenticRunStatus.QUEUED and not resume:
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

    progress_state = runs.schemas.AgenticRunProgress.model_validate(
        manifest_fields.get("progress") or _initial_progress(),
    ).model_dump(mode = "json")
    progress_state["active_audit_pages"] = []
    progress_state["active_repair_pages"] = []
    manifest_fields["progress"] = progress_state
    progress_lock = asyncio.Lock()
    progress_closed = False
    cancellation_observed = False

    async def check_cancel_requested() -> bool:
        nonlocal cancellation_observed
        if cancel_check is not None and await cancel_check():
            cancellation_observed = True
            return True
        return False

    async def persist_manifest(status, *, finished_at = None, error = None):
        if execution_check is not None:
            await execution_check()
        current_time = max(time.time(), now)
        record = runs.schemas.IngestionAIRunManifest(
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
        nonlocal progress_state
        kind = event.get("kind")
        if cancel_check is not None and event.get("status") == "running":
            if kind == "node" or kind in {
                "page_audit", "page_repair", "content_classification", "node_page_progress",
            }:
                if await check_cancel_requested():
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
                        if kind != "page_repair" or event.get("repair_skipped") is not True:
                            active_pages.add(page)
                        if kind == "page_repair":
                            repaired_pages = set(progress_state["repaired_pages"])
                            repaired_pages.discard(page)
                            progress_state["repaired_pages"] = sorted(repaired_pages)
                        if kind == "page_audit":
                            progress_state["audit_warning_pages"] = [
                                current for current in progress_state["audit_warning_pages"]
                                if current != page
                            ]
                    else:
                        active_pages.discard(page)
                        progress_state[completed_key] = sorted(set(progress_state[completed_key]) | {page})
                        if kind == "page_repair":
                            repaired_pages = set(progress_state["repaired_pages"])
                            if event.get("repair_skipped") is True:
                                repaired_pages.discard(page)
                            else:
                                repaired_pages.add(page)
                            progress_state["repaired_pages"] = sorted(repaired_pages)
                        if kind == "page_audit":
                            warning_pages = set(progress_state["audit_warning_pages"])
                            if event.get("warning") is True:
                                warning_pages.add(page)
                            progress_state["audit_warning_pages"] = sorted(warning_pages)
                        should_persist = True
                    progress_state[active_key] = sorted(active_pages)
            elif kind == "content_selection":
                classified_pages = event.get("classified_pages")
                if isinstance(classified_pages, list) and all(type(page) is int for page in classified_pages):
                    progress_state["classified_pages"] = sorted(
                        set(progress_state["classified_pages"]) | set(classified_pages),
                    )
                selected_count = event.get("selected_page_count")
                excluded_count = event.get("excluded_page_count")
                if (
                    type(selected_count) is int
                    and selected_count >= 0
                    and type(excluded_count) is int
                    and excluded_count >= 0
                ):
                    progress_state["selected_page_count"] = selected_count
                    progress_state["excluded_page_count"] = excluded_count
                    manifest_fields["selected_page_count"] = selected_count
                    manifest_fields["excluded_page_count"] = excluded_count
                    should_persist = True
            elif kind in {"visual_enrichment_progress", "visual_figure"}:
                for field, event_field in (
                    ("visual_candidate_count", "candidate_count"),
                    ("visual_completed_count", "completed_count"),
                    ("visual_converted_count", "converted_count"),
                    ("visual_failed_count", "failed_count"),
                ):
                    value = event.get(event_field)
                    if type(value) is int and value >= 0:
                        progress_state[field] = value
                should_persist = (
                    kind == "visual_enrichment_progress"
                    or progress_state["visual_completed_count"] >= progress_state["visual_candidate_count"]
                    or progress_state["visual_completed_count"] % 10 == 0
                )
            elif kind == "node_page_progress":
                node = event.get("node")
                completed = event.get("completed_count")
                total = event.get("total_count")
                active = event.get("active_count")
                if (
                    isinstance(node, str)
                    and node in {"materialize_processed_book", "reconcile_contents", "publish"}
                    and type(completed) is int
                    and completed >= 0
                    and type(total) is int
                    and total >= completed
                    and type(active) is int
                    and active >= 0
                    and active <= total - completed
                ):
                    node_progress = progress_state.setdefault("node_page_progress", {})
                    previous = node_progress.get(node) if isinstance(node_progress, dict) else None
                    previous_completed = previous.get("completed_count") if isinstance(previous, dict) else None
                    previous_total = previous.get("total_count") if isinstance(previous, dict) else None
                    node_progress[node] = {
                        "completed_count": completed,
                        "total_count": total,
                        "active_count": active,
                    }
                    should_persist = (
                        previous is None
                        or previous_total != total
                        or type(previous_completed) is not int
                        or completed < previous_completed
                        or completed - previous_completed >= 10
                        or completed >= total
                    )
            progress_state = runs.schemas.AgenticRunProgress.model_validate(
                progress_state,
            ).model_dump(mode = "json")
            manifest_fields["progress"] = progress_state
            if should_persist:
                try:
                    await persist_manifest(runs.schemas.AgenticRunStatus.RUNNING)
                except Exception as error:
                    logger.warning(
                        f"[bd-agentic-progress] manifest update failed for {agentic_run_id}: "
                        f"{type(error).__name__}: {error}"
                    )
            await progress.service.emit(
                book_id,
                agentic_run_id,
                kind if isinstance(kind, str) else "progress",
                **{key: value for key, value in event.items() if key != "kind"},
                progress = progress_state,
            )

    if existing_manifest is None:
        await persist_manifest(runs.schemas.AgenticRunStatus.QUEUED)
    await persist_manifest(runs.schemas.AgenticRunStatus.RUNNING)

    source_page_images = None
    default_text_repair = text_repair is None
    try:
        if auditor is None and finding_verifier is None:
            finding_verifier = ai.bd.agents.ingestion.finding_verifier.agent.finding_verifier()
        if auditor is None:
            auditor = ai.bd.agents.ingestion.page_auditor.agent.page_auditor()
        if text_repair is None:
            text_repair = ai.bd.agents.ingestion.text_repair.agent.text_repair()
        if patch_verifier is None and default_text_repair:
            try:
                patch_verifier = ai.bd.agents.ingestion.text_patch_verifier.agent.text_patch_verifier()
            except Exception as error:
                logger.warning(
                    f"[bd-page-evidence] visual verifier unavailable for {book_id}/{agentic_run_id}: "
                    f"{type(error).__name__}: {error}"
                )
        if structure_repair is None:
            structure_repair = ai.bd.agents.ingestion.structure_repair.agent.structure_repair()
        if metadata_normalizer is None:
            metadata_normalizer = ai.bd.agents.ingestion.metadata_normalizer.agent.metadata_normalizer()
        if content_classifier is None:
            content_classifier = ai.bd.agents.ingestion.content_classifier.agent.content_classifier()

        visual_extractor = None
        read_source_figure = None
        if any(page_ref.get("figures") for page_ref in source_page_refs):
            try:
                visual_extractor = ai.bd.agents.ingestion.visual_extractor.agent.visual_extractor()

                async def load_source_figure(name: str) -> bytes:
                    key = source.storage.keys.figure_key(book_id, name, extraction_id)
                    return await storage.read_bytes(key)
                read_source_figure = load_source_figure
            except Exception as error:
                logger.warning(
                    "[bd-visual-enrichment] extractor unavailable for %s/%s: %s",
                    book_id,
                    agentic_run_id,
                    type(error).__name__,
                )
                visual_extractor = None

        async def write_artifact(key: str, content: str, content_type: str) -> None:
            if execution_check is not None:
                await execution_check()
            if key == output_manifest_key:
                value = json.loads(content)
                value["progress"] = progress_state
                if value.get("status") == runs.schemas.AgenticRunStatus.DONE.value:
                    await settings.llm_counter.service.snapshot("bd", agentic_run_id)
                content = json.dumps(
                    value, ensure_ascii = False, separators = (",", ":"), sort_keys = True,
                )
            await storage.write(key, content, content_type = content_type)

        source_metadata = {
            key: source_manifest[key]
            for key in ("filename", "title", "subtitle", "authors", "source_format")
            if key in source_manifest
        }
        read_source_page_image = None
        if (
            patch_verifier is not None
            and source_manifest.get("source_format", "pdf") == "pdf"
            and (
                type(source_manifest.get("size_bytes")) is not int
                or source_manifest["size_bytes"] <= params.SOURCE_PDF_VERIFY_MAX_BYTES
            )
        ):
            try:
                source_page_images = source_pages.service.PdfPageImageReader(
                    lambda: source.storage.service.read_source(storage, book_id, "pdf"),
                    source_size_bytes = source_manifest.get("size_bytes"),
                    expected_page_count = len(source_page_refs),
                )
                read_source_page_image = source_page_images.read_page
            except Exception as error:
                logger.warning(
                    f"[bd-page-evidence] source-page renderer unavailable for {book_id}/{agentic_run_id}: "
                    f"{type(error).__name__}"
                )
        graph_state: ai.bd.graphs.ingestion.states.IngestionRunState = {
            "book_id": book_id,
            "extraction_run_id": extraction_id,
            "agentic_run_id": agentic_run_id,
            "workflow_version": versions.WORKFLOW_VERSION,
            "source_manifest_key": source_manifest_key,
            "source_manifest_sha256": source_manifest_sha256,
            "run_created_at": now,
            "expected_page_count": len(source_page_refs),
            "source_page_refs": source_page_refs,
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
            progress_callback = report_progress,
            cancel_check = check_cancel_requested if cancel_check is not None else None,
            finding_verifier = finding_verifier,
            patch_verifier = patch_verifier,
            read_source_page_image = read_source_page_image,
            visual_extractor = visual_extractor,
            read_source_figure = read_source_figure,
        )
        config = {"configurable": {
            "thread_id": f"bd-ingestion-agentic:{book_id}:{extraction_id}:{agentic_run_id}",
        }}
        replay_from_node = manifest_fields.get("replay_from_node")
        if checkpointer is None:
            result = await graph.ainvoke(graph_state, context = dependencies)
        else:
            snapshot = await graph.aget_state(config)
            if replay_from_node is not None:
                if replay_from_node not in params.GRAPH_NODES:
                    raise ValueError(f"unknown saved ingestion replay node {replay_from_node!r}")
                replay_config, _ = await _checkpoint_before_ingestion_node(graph, config, replay_from_node)
                descendants = set(params.REPLAY_DESCENDANTS[replay_from_node])
                reset_state: dict[str, Any] = {}
                if "audit_batch" in descendants:
                    reset_state.update(page_refs = [], page_audits = [])
                if "classify_content" in descendants:
                    reset_state.update(
                        content_selection = [],
                        content_selection_key = "",
                        content_selection_sha256 = "",
                        content_selection_revision = 0,
                        selected_page_count = 0,
                        excluded_page_count = 0,
                    )
                if "plan_repairs" in descendants:
                    reset_state["work_items"] = []
                if "repair_pages" in descendants:
                    reset_state["page_results"] = []
                if "normalize_metadata" in descendants:
                    reset_state.update(book_metadata = {}, book_metadata_sha256 = "")
                if "materialize_processed_book" in descendants:
                    reset_state.update(
                        processed_book_candidate_key = "",
                        processed_page_count = 0,
                        processed_book_revision = 0,
                    )
                if "reconcile_contents" in descendants:
                    reset_state.update(
                        processed_book_key = "",
                        processed_book_sha256 = "",
                        contents_status = "",
                    )
                reset_state["status"] = "running"
                reset_state["replay_from_node"] = replay_from_node
                node_status = (manifest_fields.get("progress") or {}).get("node_status", {})
                replay_skip_nodes = [
                    replay_node
                    for replay_node in (
                        "classify_content", "plan_repairs", "repair_pages", "normalize_metadata",
                    )
                    if replay_node not in descendants
                    and node_status.get(replay_node) == runs.schemas.AgenticNodeStatus.DONE.value
                ]
                reset_state["replay_skip_nodes"] = replay_skip_nodes
                replay_state_fields = {
                    "classify_content": (
                        "content_selection",
                        "content_selection_key",
                        "content_selection_sha256",
                        "content_selection_revision",
                        "selected_page_count",
                        "excluded_page_count",
                    ),
                    "plan_repairs": ("work_items",),
                    "repair_pages": ("page_results",),
                    "normalize_metadata": ("book_metadata", "book_metadata_sha256"),
                }
                for skipped_node in reset_state["replay_skip_nodes"]:
                    for field in replay_state_fields.get(skipped_node, ()):
                        if field in snapshot.values:
                            reset_state[field] = snapshot.values[field]
                if replay_from_node in {"classify_content", "normalize_metadata"}:
                    replay_config = await graph.aupdate_state(
                        replay_config,
                        reset_state,
                        as_node = "audit_batch",
                    )
                else:
                    replay_config = await graph.aupdate_state(replay_config, reset_state)
                config = replay_config
                logger.info(
                    "[bd-agentic] replaying from selected node book_id=%s run_id=%s node=%s",
                    book_id,
                    agentic_run_id,
                    replay_from_node,
                )
                graph_input = None
            elif snapshot.next:
                if manifest_fields.get("workflow_version") != versions.WORKFLOW_VERSION:
                    raise ValueError(
                        "This run uses an older ingestion graph checkpoint. Wipe this run and start a new "
                        "Agentic AI run to use the current graph structure."
                    )
                graph_input = None
                pending_nodes = tuple(snapshot.next)
                checkpoint_id = snapshot.config.get("configurable", {}).get("checkpoint_id")
                logger.info(
                    f"[bd-agentic] resuming checkpoint book_id={book_id} agentic_run_id={agentic_run_id} "
                    f"pending_nodes={pending_nodes} checkpoint_id={checkpoint_id}"
                )
                reported_node = progress_state.get("current_node")
                if reported_node and reported_node not in pending_nodes:
                    logger.warning(
                        f"[bd-agentic] saved progress node {reported_node!r} differs from checkpoint "
                        f"pending nodes {pending_nodes!r} for run {agentic_run_id}; checkpoint is authoritative"
                    )
            else:
                manifest_fields["workflow_version"] = versions.WORKFLOW_VERSION
                graph_input = graph_state
                logger.info(
                    f"[bd-agentic] starting graph from entry for book_id={book_id} "
                    f"agentic_run_id={agentic_run_id}; no pending checkpoint task exists"
                )
            # Sync durability makes each completed node checkpoint durable before the graph
            # advances, so a worker stop or node exception can resume at the pending node.
            result = await graph.ainvoke(
                graph_input,
                config = config,
                context = dependencies,
                durability = "sync",
            )
        final_manifest = json.loads(await storage.read_text(output_manifest_key))
        final_manifest["replay_from_node"] = None
        final_manifest["progress"] = runs.schemas.AgenticRunProgress.model_validate(
            progress_state,
        ).model_dump(mode = "json")
        if execution_check is not None:
            await execution_check()
        await storage.write(
            output_manifest_key,
            json.dumps(final_manifest, ensure_ascii = False, separators = (",", ":"), sort_keys = True),
            content_type = "application/json",
        )
        await progress.service.emit(
            book_id,
            agentic_run_id,
            "run_done",
            status = runs.schemas.AgenticRunStatus.DONE.value,
            progress = progress_state,
        )
        return result
    except progress.service.RunExecutionLeaseLostError:
        async with progress_lock:
            progress_closed = True
        raise
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
            _clear_node_page_activity(progress_state)
            progress_state["current_node"] = None
            progress_state["node_status"] = {
                node: (
                    runs.schemas.AgenticNodeStatus.PENDING.value
                    if status == runs.schemas.AgenticNodeStatus.RUNNING.value
                    else status
                )
                for node, status in progress_state["node_status"].items()
            }
            manifest_fields["progress"] = progress_state
        await settings.llm_counter.service.snapshot("bd", agentic_run_id)
        await persist_manifest(
            runs.schemas.AgenticRunStatus.CANCELLED,
            finished_at = cancelled_at,
        )
        await progress.service.emit(
            book_id,
            agentic_run_id,
            "run_cancelled",
            status = runs.schemas.AgenticRunStatus.CANCELLED.value,
            progress = progress_state,
        )
        return {
            "book_id": book_id,
            "extraction_run_id": extraction_id,
            "agentic_run_id": agentic_run_id,
            "status": runs.schemas.AgenticRunStatus.CANCELLED.value,
            "progress": progress_state,
        }
    except Exception as error:
        failed_at = max(time.time(), now)
        async with progress_lock:
            progress_closed = True
            progress_state["active_audit_pages"] = []
            progress_state["active_repair_pages"] = []
            progress_state["active_classification_pages"] = []
            _clear_node_page_activity(progress_state)
            current_node = progress_state.get("current_node")
            if not current_node:
                current_node = next(
                    (
                        node for node in params.GRAPH_NODES
                        if progress_state.get("node_status", {}).get(node)
                        != runs.schemas.AgenticNodeStatus.DONE.value
                    ),
                    None,
                )
            if current_node in progress_state.get("node_status", {}):
                progress_state["node_status"][current_node] = runs.schemas.AgenticNodeStatus.FAILED.value
            progress_state["current_node"] = current_node
            manifest_fields["progress"] = progress_state
        error_message = _safe_error_message(error)
        try:
            await settings.llm_counter.service.snapshot("bd", agentic_run_id)
            await persist_manifest(
                runs.schemas.AgenticRunStatus.FAILED,
                finished_at = failed_at,
                error = error_message,
            )
            await progress.service.emit(
                book_id,
                agentic_run_id,
                "run_failed",
                status = runs.schemas.AgenticRunStatus.FAILED.value,
                error = error_message,
                progress = progress_state,
            )
        except Exception:
            pass
        raise
    finally:
        if source_page_images is not None:
            try:
                await source_page_images.close()
            except Exception as error:
                logger.warning(
                    f"[bd-page-evidence] cleanup failed for {book_id}/{agentic_run_id}: "
                    f"{type(error).__name__}"
                )
