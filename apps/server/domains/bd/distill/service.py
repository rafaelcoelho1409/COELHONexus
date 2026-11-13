"""Run identity, immutable artifacts, lifecycle, and LangGraph execution for Distill."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from typing import Any

import domains
import infra

from . import keys, params, progress, schemas, versions

logger = logging.getLogger(__name__)


class DistillWorkerVersionMismatch(RuntimeError):
    """Raised when Celery receives a run created for a newer Distill workflow."""


def _failure_message(error: Exception) -> str:
    if isinstance(error, DistillWorkerVersionMismatch):
        return str(error)
    provider_kind = domains.settings.chat.domain.classify_error(error)
    provider_reasons = {
        "timeout": "repeated LLM provider timeouts",
        "rate_limit": "LLM provider rate limits",
        "server_error": "repeated LLM provider server errors",
    }
    reason = provider_reasons.get(provider_kind)
    if reason:
        return f"Distill failed after {reason}. Completed work is saved; resume this run to continue."
    return f"Distill failed ({type(error).__name__}). Check worker logs for details."


def _safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_") for character in value
    )


def _canonical_sha256(value: dict) -> str:
    body = json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def _write_manifest(storage: infra.minio.service.MinIOStorage, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["updated_at"] = max(time.time(), manifest["created_at"])
    validated = schemas.DistillRunManifest.model_validate(manifest)
    body = json.dumps(validated.model_dump(mode = "json"), ensure_ascii = False, separators = (",", ":"), sort_keys = True)
    await storage.write(keys.manifest_key(manifest["book_id"], manifest["run_id"]), body, content_type = params.JSON_TYPE)
    return validated.model_dump(mode = "json")


async def read_run(
    book_id: str,
    run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    manifest_key = keys.manifest_key(book_id, run_id)
    if not await storage.exists(manifest_key):
        return None
    manifest = schemas.DistillRunManifest.model_validate_json(await storage.read_text(manifest_key))
    if manifest.book_id != book_id or manifest.run_id != run_id:
        raise ValueError("the stored Distill manifest does not match the requested run")
    return manifest.model_dump(mode = "json")


async def read_current_run(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    if storage is None:
        storage = infra.minio.service.get_storage()
    pointer_key = keys.current_run_key(book_id)
    if not await storage.exists(pointer_key):
        return None
    try:
        pointer = json.loads(await storage.read_text(pointer_key))
    except json.JSONDecodeError:
        return None
    run_id = pointer.get("run_id") if isinstance(pointer, dict) else None
    if not _safe_segment(run_id):
        return None
    run = await read_run(book_id, run_id, storage = storage)
    source = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if run is None or source is None or source.get("extraction_id") != run["extraction_run_id"]:
        return None
    return run


async def list_runs(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> list[dict[str, Any]]:
    if storage is None:
        storage = infra.minio.service.get_storage()
    run_ids = await storage.list_subfolders(keys.runs_prefix(book_id))
    runs = await asyncio.gather(*(
        read_run(book_id, run_id, storage = storage)
        for run_id in run_ids if _safe_segment(run_id)
    ))
    return sorted((run for run in runs if run is not None), key = lambda run: run["created_at"], reverse = True)


async def create_distill_run(
    book_id: str,
    *,
    expected_ingestion_ai_run_id: str | None = None,
    run_id: str | None = None,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    """Create one immutable run pinned to the completed extraction and QA candidate."""
    if not _safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if expected_ingestion_ai_run_id is not None and not _safe_segment(expected_ingestion_ai_run_id):
        raise ValueError("ingestion_ai_run_id must be a safe path segment")
    run_id = run_id or uuid.uuid4().hex
    if not _safe_segment(run_id):
        raise ValueError("run_id must be a safe path segment")
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
        source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
        if source_manifest is None or source_manifest.get("status") != domains.bd.ingestion.storage.params.STATUS_DONE:
            raise ValueError("book extraction must be complete before Distill can start")
        extraction_run_id = source_manifest.get("extraction_id")
        if not _safe_segment(extraction_run_id):
            raise ValueError("the completed extraction has no valid run ID")
        source_manifest_key = domains.bd.ingestion.storage.keys.manifest_key(book_id)
        source_manifest_sha256 = _canonical_sha256(source_manifest)
        page_count = source_manifest.get("n_pages")
        if type(page_count) is not int or page_count < 1:
            raise ValueError("the completed extraction has no valid page count")

        if expected_ingestion_ai_run_id is None:
            qa_manifest = await domains.bd.ingestion.ai.service.read_current_ingestion_ai_run(book_id, storage = storage)
        else:
            qa_manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(
                book_id, expected_ingestion_ai_run_id, storage = storage,
            )
        if qa_manifest is None:
            raise ValueError("a completed ingestion QA run is required before Distill")
        qa_run_id = qa_manifest["agentic_run_id"]
        qa_manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(book_id, qa_run_id)
        qa_manifest_content = await storage.read_text(qa_manifest_key)
        qa_manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest.model_validate_json(
            qa_manifest_content,
        ).model_dump(mode = "json")
        if qa_manifest.get("status") != domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE.value:
            raise ValueError("the selected ingestion QA run is not complete")
        if qa_manifest.get("quality_status") not in {"complete", "complete_with_review"}:
            raise ValueError("the selected ingestion QA run has no publishable quality result")
        if qa_manifest.get("extraction_run_id") != extraction_run_id:
            raise ValueError("the selected ingestion QA run is pinned to a different extraction")
        if qa_manifest.get("source_manifest_sha256") != source_manifest_sha256:
            raise ValueError("the selected ingestion QA run refers to a different source manifest")
        if qa_manifest.get("expected_page_count") != page_count:
            raise ValueError("the selected ingestion QA run does not cover every page")

        selection_result = await domains.bd.ingestion.ai.service.read_selection(
            book_id, qa_run_id, storage = storage,
        )
        if selection_result is None:
            raise ValueError("a completed content-selection pass is required before Distill can start")
        selection, selection_key, selection_sha256 = selection_result
        if (
            selection.extraction_run_id != extraction_run_id
            or selection.expected_page_count != page_count
        ):
            raise ValueError("the content selection is pinned to a different extraction or page count")
        selected_page_count = sum(page.disposition == "include" for page in selection.pages)
        if selected_page_count < 1:
            raise ValueError("at least one page must be included in the Distill selection")

        active = [
            run for run in await list_runs(book_id, storage = storage)
            if run["status"] in {"queued", "running"}
        ]
        if active:
            raise ValueError("another Distill run is already active for this book")
        manifest_key = keys.manifest_key(book_id, run_id)
        if await storage.exists(manifest_key):
            raise ValueError("run_id already exists; use a new ID to preserve immutable run history")
        qa_manifest_sha256 = hashlib.sha256(qa_manifest_content.encode("utf-8")).hexdigest()

        domains.settings.chat.service.refresh_endpoint(force = True)
        now = time.time()
        manifest = schemas.DistillRunManifest(
            run_id = run_id,
            book_id = book_id,
            extraction_run_id = extraction_run_id,
            source_manifest_key = source_manifest_key,
            source_manifest_sha256 = source_manifest_sha256,
            ingestion_ai_run_id = qa_run_id,
            ingestion_ai_manifest_key = qa_manifest_key,
            ingestion_ai_manifest_sha256 = qa_manifest_sha256,
            expected_page_count = page_count,
            selected_page_count = selected_page_count,
            content_selection_key = selection_key,
            content_selection_sha256 = selection_sha256,
            content_selection_revision = selection.revision,
            prompt_version = versions.PROMPT_VERSION,
            model_name = domains.settings.chat.service.ENDPOINT.model,
            created_at = now,
            updated_at = now,
            progress = schemas.DistillProgress(
                node_status = {node: "pending" for node in params.GRAPH_NODES},
            ),
        ).model_dump(mode = "json")
        manifest = await _write_manifest(storage, manifest)
        await progress.service.clear(book_id, run_id)
        await storage.write(
            keys.current_run_key(book_id),
            json.dumps({"run_id": run_id, "extraction_run_id": extraction_run_id}, separators = (",", ":")),
            content_type = params.JSON_TYPE,
        )
        await progress.service.emit(book_id, run_id, "run_queued", status = manifest["status"], progress = manifest["progress"])
        return manifest


async def queue_resume(book_id: str, run_id: str) -> dict[str, Any]:
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_run(book_id, run_id)
        if manifest is None:
            raise FileNotFoundError(f"Distill run {run_id} was not found")
        if manifest["status"] not in {"failed", "cancelled"}:
            raise ValueError("only failed or cancelled Distill runs can be resumed")
        active = [run for run in await list_runs(book_id) if run["status"] in {"queued", "running"}]
        if any(run["run_id"] != run_id for run in active):
            raise ValueError("another Distill run is already active for this book")
        await progress.service.clear_cancel(book_id, run_id)
        if manifest.get("resume_from_node") not in params.GRAPH_NODES:
            failed_nodes = [
                node
                for node in params.GRAPH_NODES
                if manifest["progress"]["node_status"].get(node) in {"failed", "running"}
            ]
            unfinished_nodes = [
                node
                for node in params.GRAPH_NODES
                if manifest["progress"]["node_status"].get(node) != "done"
            ]
            manifest["resume_from_node"] = (
                failed_nodes[0]
                if failed_nodes
                else "publish_report"
                if not unfinished_nodes
                else manifest["progress"].get("current_node")
                or (unfinished_nodes[0] if unfinished_nodes else "prepare_units")
            )
        manifest["status"] = "queued"
        manifest["workflow_version"] = versions.WORKFLOW_VERSION
        manifest["prompt_version"] = versions.PROMPT_VERSION
        manifest["finished_at"] = None
        manifest["error"] = None
        manifest["updated_at"] = time.time()
        manifest["progress"]["current_node"] = None
        for node, status in manifest["progress"]["node_status"].items():
            if status in {"running", "failed"}:
                manifest["progress"]["node_status"][node] = "pending"
        return await _write_manifest(infra.minio.service.get_storage(), manifest)


async def request_cancel(book_id: str, run_id: str) -> dict[str, Any]:
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_run(book_id, run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError(f"Distill run {run_id} was not found")
        if manifest["status"] == "queued":
            manifest["status"] = "cancelled"
            manifest["finished_at"] = max(time.time(), manifest["created_at"])
            manifest["error"] = None
            manifest["progress"]["current_node"] = None
            manifest = await _write_manifest(storage, manifest)
            await progress.service.emit(
                book_id,
                run_id,
                "run_cancelled",
                status = "cancelled",
                progress = manifest["progress"],
            )
        elif manifest["status"] == "running":
            await progress.service.request_cancel(book_id, run_id)
            await progress.service.emit(book_id, run_id, "run_cancel_requested", status = manifest["status"])
        return {"book_id": book_id, "run_id": run_id, "status": manifest["status"]}


async def wipe_run(book_id: str, run_id: str) -> dict[str, Any]:
    """Delete a terminal candidate, its checkpoints, usage, and progress without touching source pages."""
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    try:
        async with (
            infra.redis.service.client() as redis,
            redis.lock(
                domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
                timeout = 3600,
                blocking_timeout = 30,
            ),
            redis.lock(
                f"coelhonexus:bd:distill:pdf:{book_id}:{run_id}",
                timeout = 3600,
                blocking_timeout = 30,
            ),
            redis.lock(
                domains.bd.rag.keys.enqueue_lock_key(book_id, run_id),
                timeout = 120,
                blocking_timeout = 30,
            ),
            redis.lock(
                domains.bd.rag.keys.index_lock_key(book_id, run_id),
                timeout = domains.bd.rag.params.INDEX_LOCK_TIMEOUT_S,
                blocking_timeout = 1,
            ),
        ):
            manifest = await read_run(book_id, run_id, storage = storage)
            if manifest is None:
                raise FileNotFoundError(f"Distill run {run_id} was not found")
            if manifest["status"] in {"queued", "running"}:
                raise ValueError("stop the Distill run and wait for it to finish before wiping it")
            runs = await list_runs(book_id, storage = storage)
            if any(run["status"] in {"queued", "running"} for run in runs):
                raise ValueError("stop the active Distill run before wiping another run")
            index_manifest = await domains.bd.rag.service.read_index(book_id, run_id, storage = storage)
            if index_manifest is not None:
                await domains.bd.rag.service.delete_run_points(book_id, run_id)
                await domains.bd.rag.service.delete_graph_run(book_id, run_id)
            replacement = next(
                (run for run in runs
                 if run["run_id"] != run_id and run["extraction_run_id"] == manifest["extraction_run_id"]),
                None,
            )
            thread_id = f"bd-distill:{book_id}:{manifest['extraction_run_id']}:{run_id}"
            checkpointer = await infra.postgres.service.init_checkpointer()
            await checkpointer.adelete_thread(thread_id)
            removed = await storage.delete_prefix(keys.run_prefix(book_id, run_id))
            await domains.settings.llm_counter.service.delete_run("bd", run_id)
            await progress.service.clear(book_id, run_id)
            pointer_key = keys.current_run_key(book_id)
            if await storage.exists(pointer_key):
                try:
                    pointer = json.loads(await storage.read_text(pointer_key))
                except json.JSONDecodeError:
                    pointer = None
                if isinstance(pointer, dict) and pointer.get("run_id") == run_id:
                    await storage.write(
                        pointer_key,
                        json.dumps(
                            {
                                "run_id": replacement["run_id"] if replacement else None,
                                "extraction_run_id": manifest["extraction_run_id"],
                            },
                            separators = (",", ":"),
                        ),
                        content_type = params.JSON_TYPE,
                    )
            return {"book_id": book_id, "run_id": run_id, "wiped": True, "objects_deleted": removed}
    except infra.redis.service.LockError as error:
        raise ValueError("a Distill retrieval index is still processing; wait for it before wiping this run") from error


async def mark_failed(book_id: str, run_id: str, error: Exception) -> dict[str, Any] | None:
    manifest = await read_run(book_id, run_id)
    if manifest is None or manifest["status"] in {"done", "failed", "cancelled"}:
        return manifest
    manifest["status"] = "failed"
    manifest["finished_at"] = max(time.time(), manifest["created_at"])
    manifest["error"] = _failure_message(error)
    manifest["progress"]["current_node"] = None
    await _write_manifest(infra.minio.service.get_storage(), manifest)
    await progress.service.emit(book_id, run_id, "run_failed", status = manifest["status"], error = manifest["error"])
    return manifest


async def read_report(book_id: str, run_id: str) -> tuple[dict[str, Any], str]:
    manifest = await read_run(book_id, run_id)
    if manifest is None:
        raise FileNotFoundError(f"Distill run {run_id} was not found")
    if manifest["status"] != "done" or not manifest.get("report_key"):
        raise ValueError("the Learning Guide is not ready")
    content = await infra.minio.service.get_storage().read_text(manifest["report_key"])
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != manifest.get("report_sha256"):
        raise ValueError("the stored Learning Guide failed its integrity check")
    report = schemas.StudyGuideDocument.model_validate_json(content)
    return report.model_dump(mode = "json"), content


async def read_html(book_id: str, run_id: str) -> str:
    manifest = await read_run(book_id, run_id)
    if manifest is None:
        raise FileNotFoundError(f"Distill run {run_id} was not found")
    if manifest["status"] != "done" or not manifest.get("html_key"):
        raise ValueError("the Learning Guide is not ready")
    content = await infra.minio.service.get_storage().read_text(manifest["html_key"])
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != manifest.get("html_sha256"):
        raise ValueError("the stored Learning Guide HTML failed its integrity check")
    return content


async def _restore_chapter_resume_state(
    ai_module: Any,
    dependencies: Any,
    initial_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Rehydrate chapter-stage state from durable source and lesson artifacts."""
    prepared = await ai_module.bd.nodes.distill.extract_learning_units.service.prepare_units(
        dependencies,
        initial_state,
    )
    completed_units = []
    for ref in prepared.get("unit_refs", []):
        artifact_key = keys.learning_unit_key(
            initial_state["book_id"], initial_state["run_id"], ref["unit_id"],
        )
        if not await dependencies.storage.exists(artifact_key):
            return None
        artifact = json.loads(await dependencies.storage.read_text(artifact_key))
        if artifact.get("text_sha256") != ref.get("text_sha256"):
            raise ValueError("a persisted learning-unit artifact no longer matches its source input")
        lesson_ids = [
            item["learning_unit_id"]
            for item in artifact.get("lessons", [])
            if isinstance(item, dict) and isinstance(item.get("learning_unit_id"), str)
        ]
        completed_units.append({
            **ref,
            "learning_unit_ids": lesson_ids,
            "existing_artifact": True,
        })
    return {
        **initial_state,
        **prepared,
        "completed_units": completed_units,
    }


async def mark_run_started(book_id: str, run_id: str) -> dict[str, Any]:
    """Promote a queued run as soon as a worker accepts it, before checkpointer setup."""
    storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        manifest = await read_run(book_id, run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError(f"Distill run {run_id} was not found")
        if manifest["status"] in {"done", "cancelled"}:
            return manifest
        if manifest["status"] not in {"queued", "running"}:
            raise ValueError("only queued or running Distill runs can start")
        if manifest["status"] == "queued":
            manifest["status"] = "running"
            manifest["error"] = None
            manifest["finished_at"] = None
            manifest = await _write_manifest(storage, manifest)
            await progress.service.emit(book_id, run_id, "run_started", status = "running", progress = manifest["progress"])
        return manifest


async def read_pdf(book_id: str, run_id: str) -> bytes:
    """Return a cached PDF or render and persist the immutable companion artifact."""
    manifest = await read_run(book_id, run_id)
    if manifest is None:
        raise FileNotFoundError(f"Distill run {run_id} was not found")
    if manifest["status"] != "done":
        raise ValueError("the Learning Guide is not ready")
    storage = infra.minio.service.get_storage()
    pdf_key = manifest.get("pdf_key")
    if pdf_key:
        if not await storage.exists(pdf_key):
            raise ValueError("the stored Learning Guide PDF is missing")
        pdf = await storage.read_bytes(pdf_key)
        if hashlib.sha256(pdf).hexdigest() != manifest.get("pdf_sha256"):
            raise ValueError("the stored Learning Guide PDF failed its integrity check")
        return pdf

    report_html = await read_html(book_id, run_id)
    pdf = await domains.bd.distill.rendering.service.render_pdf(report_html)
    digest = hashlib.sha256(pdf).hexdigest()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            f"coelhonexus:bd:distill:pdf:{book_id}:{run_id}",
            timeout = 30,
            blocking_timeout = 30,
        ),
    ):
        current = await read_run(book_id, run_id, storage = storage)
        if current is None or current["status"] != "done":
            raise ValueError("the Learning Guide changed while its PDF was rendering")
        if current.get("pdf_key"):
            stored = await storage.read_bytes(current["pdf_key"])
            if hashlib.sha256(stored).hexdigest() != current.get("pdf_sha256"):
                raise ValueError("the stored Learning Guide PDF failed its integrity check")
            return stored
        pdf_key = keys.pdf_key(book_id, run_id)
        await storage.write(pdf_key, pdf, content_type = params.PDF_TYPE)
        current["pdf_key"] = pdf_key
        current["pdf_sha256"] = digest
        await _write_manifest(storage, current)
    return pdf


async def run_distill(
    book_id: str,
    run_id: str,
    *,
    checkpointer: Any | None,
) -> dict[str, Any]:
    """Run or resume the pinned graph; all substantive intermediate artifacts remain outside graph state."""
    from langgraph.errors import NodeCancelledError

    import ai

    storage = infra.minio.service.get_storage()
    manifest = await read_run(book_id, run_id, storage = storage)
    if manifest is None:
        raise FileNotFoundError(f"Distill run {run_id} was not found")
    if manifest["status"] in {"done", "cancelled"}:
        return manifest
    if manifest["status"] not in {"queued", "running"}:
        raise ValueError("only queued or running Distill runs can execute")
    worker_workflow_version = versions.WORKFLOW_VERSION
    worker_prompt_version = versions.PROMPT_VERSION
    run_workflow_version = manifest.get("workflow_version")
    run_prompt_version = manifest.get("prompt_version")
    logger.info(
        "[bd-distill] worker versions workflow=%s prompt=%s run=%s expected_workflow=%s expected_prompt=%s",
        worker_workflow_version,
        worker_prompt_version,
        run_id,
        run_workflow_version,
        run_prompt_version,
    )
    if (run_workflow_version, run_prompt_version) != (worker_workflow_version, worker_prompt_version):
        raise DistillWorkerVersionMismatch(
            "Distill worker code is out of date "
            f"(worker workflow {worker_workflow_version}/prompt {worker_prompt_version}; "
            f"run requires {run_workflow_version}/prompt {run_prompt_version}). "
            "Redeploy the Celery worker before resuming."
        )

    source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if source_manifest is None or source_manifest.get("extraction_id") != manifest["extraction_run_id"]:
        raise ValueError("the book extraction changed after this Distill run was created")
    if _canonical_sha256(source_manifest) != manifest["source_manifest_sha256"]:
        raise ValueError("the source manifest changed after this Distill run was created")
    qa_content = await storage.read_text(manifest["ingestion_ai_manifest_key"])
    if hashlib.sha256(qa_content.encode("utf-8")).hexdigest() != manifest["ingestion_ai_manifest_sha256"]:
        raise ValueError("the ingestion QA manifest changed after this Distill run was created")
    if manifest.get("schema_version") == "1.1":
        selection_content = await storage.read_text(manifest["content_selection_key"])
        if hashlib.sha256(selection_content.encode("utf-8")).hexdigest() != manifest["content_selection_sha256"]:
            raise ValueError("the content-selection revision changed after this Distill run was created")
        selection = domains.bd.ingestion.ai.schemas.ContentSelectionManifest.model_validate_json(selection_content)
        if (
            selection.book_id != book_id
            or selection.extraction_run_id != manifest["extraction_run_id"]
            or selection.agentic_run_id != manifest["ingestion_ai_run_id"]
            or selection.revision != manifest["content_selection_revision"]
            or sum(page.disposition == "include" for page in selection.pages) != manifest["selected_page_count"]
        ):
            raise ValueError("the pinned content-selection revision does not match this Distill run")

    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        current = await read_run(book_id, run_id, storage = storage)
        if current is None:
            raise FileNotFoundError(f"Distill run {run_id} was not found")
        if current["status"] in {"done", "cancelled"}:
            return current
        if current["status"] not in {"queued", "running"}:
            raise ValueError("only queued or running Distill runs can execute")
        manifest = current
        if manifest["status"] == "queued":
            manifest["status"] = "running"
            manifest["error"] = None
            manifest["finished_at"] = None
            manifest = await _write_manifest(storage, manifest)
            await progress.service.emit(book_id, run_id, "run_started", status = "running", progress = manifest["progress"])
    domains.settings.chat.service.refresh_endpoint(force = True)

    existing_units = await storage.list(f"{keys.run_prefix(book_id, run_id)}learning-units/")
    existing_chapters = await storage.list(f"{keys.run_prefix(book_id, run_id)}chapters/")
    completed_unit_ids = {key.rsplit("/", 1)[-1].removesuffix(".json") for key in existing_units}
    completed_chapter_ids = {key.rsplit("/", 1)[-1].removesuffix(".json") for key in existing_chapters}
    progress_lock = asyncio.Lock()
    cancellation_observed = False
    cancel_redis = infra.redis.service.new_redis()

    async def report_progress(event: dict[str, Any]) -> None:
        nonlocal cancellation_observed, manifest
        kind = event.get("kind")
        status = event.get("status")
        should_check_cancel = (kind == "node" and status == "running") or kind in {"unit", "chapter"}
        if should_check_cancel and await progress.service.cancel_requested(cancel_redis, book_id, run_id):
            cancellation_observed = True
            raise asyncio.CancelledError
        async with progress_lock:
            current = manifest["progress"]
            if kind == "node":
                node = event.get("node")
                if isinstance(node, str) and node in current["node_status"] and status in {"running", "done", "failed"}:
                    current["node_status"][node] = status
                    if status == "failed":
                        manifest["resume_from_node"] = node
                    elif status == "done" and manifest.get("resume_from_node") == node:
                        manifest["resume_from_node"] = None
                    current["current_node"] = node if status == "running" else (
                        None if current.get("current_node") == node else current.get("current_node")
                    )
            elif kind == "unit_plan":
                current["total_units"] = int(event.get("total_units", 0))
            elif kind == "unit" and status == "done":
                unit_id = event.get("unit_id")
                if isinstance(unit_id, str):
                    completed_unit_ids.add(unit_id)
                current["completed_units"] = len(completed_unit_ids)
            elif kind == "chapter_plan":
                current["total_chapters"] = int(event.get("total_chapters", 0))
            elif kind == "chapter" and status == "done":
                chapter_id = event.get("chapter_id")
                if isinstance(chapter_id, str):
                    completed_chapter_ids.add(chapter_id)
                current["completed_chapters"] = len(completed_chapter_ids)
            manifest = await _write_manifest(storage, manifest)
            await progress.service.emit(book_id, run_id, kind if isinstance(kind, str) else "progress", **{
                key: value for key, value in event.items() if key != "kind"
            }, progress = manifest["progress"])

    try:
        extractor = ai.bd.agents.distill.learning_extractor.agent.learning_extractor()
        dependencies = ai.bd.graphs.distill.entities.DistillDependencies(
            storage = storage,
            extractor = extractor,
            controls = ai.bd.graphs.distill.entities.DistillRunControls(
                chapter_synthesis_semaphore = asyncio.Semaphore(
                    params.CHAPTER_SYNTHESIS_CONCURRENCY,
                ),
            ),
            progress_callback = report_progress,
        )
        graph = ai.bd.graphs.distill.graphs.distill_graph(dependencies, checkpointer = checkpointer)
        initial_state = {
            "book_id": book_id,
            "run_id": run_id,
            "extraction_run_id": manifest["extraction_run_id"],
            "ingestion_ai_run_id": manifest["ingestion_ai_run_id"],
            "source_manifest_key": manifest["source_manifest_key"],
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "ingestion_ai_manifest_key": manifest["ingestion_ai_manifest_key"],
            "ingestion_ai_manifest_sha256": manifest["ingestion_ai_manifest_sha256"],
            "content_selection_key": manifest.get("content_selection_key"),
            "content_selection_sha256": manifest.get("content_selection_sha256"),
            "content_selection_revision": manifest.get("content_selection_revision", 1),
            "expected_page_count": manifest["expected_page_count"],
            "selected_page_count": manifest.get("selected_page_count", manifest["expected_page_count"]),
            "run_created_at": manifest["created_at"],
            "resume_from_node": manifest.get("resume_from_node"),
            "completed_units": [],
            "completed_chapters": [],
        }
        graph_config = {
            "configurable": {"thread_id": f"bd-distill:{book_id}:{manifest['extraction_run_id']}:{run_id}"},
            "max_concurrency": params.GRAPH_CONCURRENCY,
        }
        completed_checkpoint_state = None
        if checkpointer is not None:
            snapshot = await graph.aget_state(graph_config)
            if snapshot.next:
                graph_input = None
                logger.info(
                    "[bd-distill] resuming LangGraph checkpoint run=%s pending_nodes=%s",
                    run_id,
                    snapshot.next,
                )
            elif manifest.get("resume_from_node") == "publish_report":
                required_resume_fields = (
                    "book_id",
                    "run_id",
                    "extraction_run_id",
                    "ingestion_ai_run_id",
                    "book_synthesis_key",
                    "source_pages",
                    "completed_units",
                    "completed_chapters",
                )
                if not isinstance(snapshot.values, dict) or any(
                    field not in snapshot.values for field in required_resume_fields
                ):
                    raise ValueError("the completed Distill checkpoint cannot resume report publication")
                completed_checkpoint_state = snapshot.values
                graph_input = None
                logger.info(
                    "[bd-distill] rerunning report publication from completed checkpoint run=%s",
                    run_id,
                )
            else:
                graph_input = initial_state
        else:
            graph_input = initial_state
        resume_from_node = manifest.get("resume_from_node")
        chapter_stage_nodes = {"prepare_chapters", "synthesize_chapter", "synthesize_book", "publish_report"}
        if graph_input is not None and resume_from_node in chapter_stage_nodes:
            restored_state = await _restore_chapter_resume_state(ai, dependencies, initial_state)
            if restored_state is None:
                resume_from_node = "prepare_units"
                manifest["resume_from_node"] = resume_from_node
                manifest = await _write_manifest(storage, manifest)
                graph_input = {**initial_state, "resume_from_node": resume_from_node}
                logger.warning(
                    "[bd-distill] chapter-stage resume lacked complete learning-unit artifacts; "
                    "restarting at extraction with artifact reuse run=%s",
                    run_id,
                )
            else:
                graph_input = {
                    **restored_state,
                    "resume_from_node": resume_from_node,
                }
                logger.info(
                    "[bd-distill] restoring %s persisted learning-unit artifacts and "
                    "resuming at chapter mapping run=%s failed_node=%s",
                    len(restored_state["completed_units"]),
                    run_id,
                    resume_from_node,
                )
        if completed_checkpoint_state is not None:
            result = await ai.bd.nodes.distill.publish_report.node.publish_report(
                dependencies,
                completed_checkpoint_state,
            )
        else:
            result = await graph.ainvoke(graph_input, config = graph_config)
        required = ("report_key", "report_sha256", "html_key", "html_sha256")
        missing = [field for field in required if not isinstance(result.get(field), str)]
        if missing:
            raise ValueError(
                "the completed graph did not publish every required report artifact: "
                + ", ".join(missing)
            )
        for key_field, hash_field in (("report_key", "report_sha256"), ("html_key", "html_sha256")):
            content = await storage.read_text(result[key_field])
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != result[hash_field]:
                raise ValueError(f"the {key_field} artifact failed its publication integrity check")
        manifest.update(
            status = "done",
            finished_at = max(time.time(), manifest["created_at"]),
            error = None,
            report_key = result["report_key"],
            report_sha256 = result["report_sha256"],
            html_key = result["html_key"],
            html_sha256 = result["html_sha256"],
            covered_page_count = result["covered_page_count"],
            uncovered_pages = result["uncovered_pages"],
            review_required = result["review_required"],
            structural_quality = result["structural_quality"],
        )
        manifest = await _write_manifest(storage, manifest)
        await domains.settings.llm_counter.service.snapshot("bd", run_id)
        await progress.service.emit(book_id, run_id, "run_done", status = "done", progress = manifest["progress"])
        return manifest
    except (asyncio.CancelledError, NodeCancelledError):
        if not cancellation_observed:
            raise
        manifest["status"] = "cancelled"
        manifest["finished_at"] = max(time.time(), manifest["created_at"])
        manifest["error"] = None
        manifest["progress"]["current_node"] = None
        manifest = await _write_manifest(storage, manifest)
        await progress.service.emit(book_id, run_id, "run_cancelled", status = "cancelled", progress = manifest["progress"])
        return manifest
    except Exception as error:
        manifest["status"] = "failed"
        manifest["finished_at"] = max(time.time(), manifest["created_at"])
        manifest["error"] = _failure_message(error)
        manifest["progress"]["current_node"] = None
        manifest = await _write_manifest(storage, manifest)
        logger.exception("[bd-distill] run failed book=%s run=%s", book_id, run_id)
        await domains.settings.llm_counter.service.snapshot("bd", run_id)
        await progress.service.emit(book_id, run_id, "run_failed", status = "failed", error = manifest["error"])
        raise
    finally:
        with contextlib.suppress(Exception):
            await cancel_redis.aclose()
