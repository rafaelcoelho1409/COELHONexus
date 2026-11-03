"""Run integration between completed Book Distiller extractions and the AI graph."""
from __future__ import annotations
import ai, domains, infra


import hashlib
import json
import time
import uuid
from typing import Any


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
        "pages": [],
        "book_metadata_key": domains.bd.ingestion.storage.keys.agentic_book_metadata_key(
            book_id, agentic_run_id,
        ),
        "quality_report_key": domains.bd.ingestion.storage.keys.agentic_quality_report_key(
            book_id, agentic_run_id,
        ),
        "created_at": created_at,
    }


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
    return manifest.model_dump(mode = "json")


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
    error_text = str(error)
    if not error_text and isinstance(error, Exception):
        error_text = error.__class__.__name__
    manifest["status"] = domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED.value
    manifest["updated_at"] = finished_at
    manifest["finished_at"] = finished_at
    manifest["error"] = error_text[:2000]
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
        return result
    except Exception as error:
        failed_at = max(time.time(), now)
        try:
            await persist_manifest(
                domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED,
                finished_at = failed_at,
                error = str(error)[:2000] or error.__class__.__name__,
            )
        except Exception:
            pass
        raise
