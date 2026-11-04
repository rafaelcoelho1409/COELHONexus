"""Run integration between completed Book Distiller extractions and the AI graph."""
from __future__ import annotations
import ai, domains, infra


import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any


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
    extraction_id, inventory_state = _inventory_state(
        book_id, source_manifest, expected_extraction_id,
    )
    _, _, source_page_refs = ai.bd.nodes.ingestion.inventory.domain.validate_request(inventory_state)
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
    manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(
        book_id, agentic_run_id,
    )
    if await storage.exists(manifest_key):
        raise ValueError("agentic_run_id already exists; use a new ID to preserve immutable run history")

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


async def read_display_page(
    book_id: str,
    page_number: int,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[str | None, str]:
    """Return a completed automatic AI page when available, otherwise the immutable Docling page."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    run = await read_current_ingestion_ai_run(book_id, storage = storage)
    if run is not None and page_number in run["progress"]["processed_pages"]:
        normalized_key = domains.bd.ingestion.storage.keys.agentic_page_key(
            book_id, run["agentic_run_id"], page_number,
        )
        try:
            return await storage.read_text(normalized_key), "agentic"
        except FileNotFoundError:
            pass
    markdown = await domains.bd.ingestion.storage.service.read_page(storage, book_id, page_number)
    return markdown, "docling"


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


async def queue_ingestion_ai_resume(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    """Requeue a failed candidate with the same source identity and graph thread."""
    manifest = await read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if manifest is None:
        raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
    if manifest["status"] != domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED.value:
        raise ValueError("only failed agentic runs can be manually resumed")
    if storage is None:
        storage = infra.minio.service.get_storage()
    _, extraction_id, _, source_manifest_sha256, _ = await _load_run_source(
        book_id, manifest["extraction_run_id"], storage,
    )
    if extraction_id != manifest["extraction_run_id"] or source_manifest_sha256 != manifest["source_manifest_sha256"]:
        raise ValueError("the failed agentic run no longer matches the current extraction")
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


def _inventory_state(
    book_id: str,
    manifest: dict,
    expected_extraction_id: str | None,
) -> tuple[str, ai.bd.graphs.ingestion.state.IngestionInventoryState]:
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

    graph_state: ai.bd.graphs.ingestion.state.IngestionInventoryState = {
        "book_id": book_id,
        "extraction_run_id": extraction_id,
        "extraction_status": manifest["status"],
        "expected_page_count": page_count,
        "page_refs": [
            {
                "page": page.get("page"),
                "key": page.get("key"),
                "pictures": page.get("pictures", 0),
            }
            for page in pages
        ],
    }
    return extraction_id, graph_state


def _page_audit_state(
    book_id: str,
    manifest: dict,
    page_number: int,
    expected_extraction_id: str | None,
) -> tuple[str, ai.bd.graphs.ingestion.state.PageAuditState]:
    if type(page_number) is not int or page_number <= 0:
        raise ValueError("page_number must be a positive integer")

    extraction_id, inventory_state = _inventory_state(
        book_id,
        manifest,
        expected_extraction_id,
    )
    page_refs = inventory_state["page_refs"]
    if page_number > len(page_refs):
        raise ValueError("page_number is outside the completed extraction")
    page_ref = page_refs[page_number - 1]
    if page_ref["page"] != page_number:
        raise ValueError("the manifest page entries must be in physical page order")

    graph_state: ai.bd.graphs.ingestion.state.PageAuditState = {
        "book_id": book_id,
        "extraction_run_id": extraction_id,
        "extraction_status": manifest["status"],
        "page_ref": page_ref,
    }
    return extraction_id, graph_state


async def run_inventory(
    book_id: str,
    *,
    expected_extraction_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    """Inventory the current completed extraction, reading only its canonical raw page objects."""
    if not _safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_extraction_id is not None and not _safe_segment(expected_extraction_id):
        raise ValueError("expected_extraction_id must be a safe path segment")
    if storage is None:
        storage = infra.minio.service.get_storage()

    manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if manifest is None:
        raise FileNotFoundError(f"book {book_id} has no ingestion manifest")

    extraction_id, graph_state = _inventory_state(book_id, manifest, expected_extraction_id)
    graph = ai.bd.graphs.ingestion.graph.build_inventory_graph(
        storage.read_text, checkpointer = checkpointer,
    )
    if checkpointer is None:
        return await graph.ainvoke(graph_state)

    config = {"configurable": {"thread_id": f"bd-ingestion-inventory:{book_id}:{extraction_id}"}}
    snapshot = await graph.aget_state(config)
    if snapshot.values.get("status") == "complete":
        return snapshot.values
    return await graph.ainvoke(graph_state, config = config)


async def run_page_audit(
    book_id: str,
    page_number: int,
    *,
    expected_extraction_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
    auditor: Any | None = None,
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    """Audit one page from the current completed extraction without checkpointing its Markdown."""
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

    extraction_id, graph_state = _page_audit_state(
        book_id,
        manifest,
        page_number,
        expected_extraction_id,
    )
    if auditor is None:
        auditor = ai.bd.agents.ingestion.page_auditor.agent.page_auditor()

    graph = ai.bd.graphs.ingestion.graph.build_page_audit_graph(
        storage.read_text,
        auditor,
        checkpointer = checkpointer,
    )
    if checkpointer is None:
        return await graph.ainvoke(graph_state)

    config = {
        "configurable": {
            "thread_id": f"bd-ingestion-page-audit:{book_id}:{extraction_id}:{page_number}",
        },
    }
    snapshot = await graph.aget_state(config)
    if snapshot.values.get("status") == "complete":
        return snapshot.values
    return await graph.ainvoke(graph_state, config = config)


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
    checkpointer: Any | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Run the full versioned AI workflow against the current immutable Docling extraction."""
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
        }
        if existing_manifest.status == domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE:
            raise ValueError("completed agentic run IDs are immutable")
        if existing_manifest.status not in resumable_statuses:
            raise ValueError("only queued, running, or failed agentic runs can be resumed")
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
        nonlocal progress_state
        kind = event.get("kind")
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
            elif kind in {"page_audit", "page_repair"}:
                page = event.get("page")
                status = event.get("status")
                if type(page) is int and status in {"running", "done"}:
                    active_key = "active_audit_pages" if kind == "page_audit" else "active_repair_pages"
                    completed_key = "audited_pages" if kind == "page_audit" else "processed_pages"
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
        if auditor is None:
            auditor = ai.bd.agents.ingestion.page_auditor.agent.page_auditor()
        if text_repair is None:
            text_repair = ai.bd.agents.ingestion.text_repair.agent.text_repair()
        if structure_repair is None:
            structure_repair = ai.bd.agents.ingestion.structure_repair.agent.structure_repair()
        if metadata_normalizer is None:
            metadata_normalizer = ai.bd.agents.ingestion.metadata_normalizer.agent.metadata_normalizer()

        async def write_artifact(key: str, content: str, content_type: str) -> None:
            if key == output_manifest_key:
                value = json.loads(content)
                value["progress"] = progress_state
                content = json.dumps(
                    value, ensure_ascii = False, separators = (",", ":"), sort_keys = True,
                )
            await storage.write(key, content, content_type = content_type)

        source_metadata = {
            key: source_manifest[key]
            for key in ("filename", "title", "subtitle", "authors", "source_format")
            if key in source_manifest
        }
        graph_state: ai.bd.graphs.ingestion.state.IngestionRunState = {
            "book_id": book_id,
            "extraction_run_id": extraction_id,
            "agentic_run_id": agentic_run_id,
            "workflow_version": domains.bd.ingestion.ai.versions.WORKFLOW_VERSION,
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
        graph = ai.bd.graphs.ingestion.graph.build_ingestion_graph(
            storage.read_text,
            write_artifact,
            auditor,
            {"text_repair": text_repair, "structure": structure_repair},
            metadata_normalizer,
            checkpointer = checkpointer,
            progress_callback = report_progress,
        )
        config = {"configurable": {
            "thread_id": f"bd-ingestion-agentic:{book_id}:{extraction_id}:{agentic_run_id}",
        }}
        if checkpointer is None:
            result = await graph.ainvoke(graph_state)
        else:
            snapshot = await graph.aget_state(config)
            graph_input = None if snapshot.next else graph_state
            result = await graph.ainvoke(graph_input, config = config)
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
