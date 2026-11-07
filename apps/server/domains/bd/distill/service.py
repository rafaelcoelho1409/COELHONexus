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
            prompt_version = versions.PROMPT_VERSION,
            model_name = domains.settings.chat.service.ENDPOINT.model,
            created_at = now,
            updated_at = now,
            progress = schemas.DistillProgress(
                node_status = {node: "pending" for node in params.GRAPH_NODES},
            ),
        ).model_dump(mode = "json")
        manifest = await _write_manifest(storage, manifest)
        await progress.clear(book_id, run_id)
        await storage.write(
            keys.current_run_key(book_id),
            json.dumps({"run_id": run_id, "extraction_run_id": extraction_run_id}, separators = (",", ":")),
            content_type = params.JSON_TYPE,
        )
        await progress.emit(book_id, run_id, "run_queued", status = manifest["status"], progress = manifest["progress"])
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
        await progress.clear_cancel(book_id, run_id)
        manifest["status"] = "queued"
        manifest["finished_at"] = None
        manifest["error"] = None
        manifest["updated_at"] = time.time()
        manifest["progress"]["current_node"] = None
        for node, status in manifest["progress"]["node_status"].items():
            if status in {"running", "failed"}:
                manifest["progress"]["node_status"][node] = "pending"
        return await _write_manifest(infra.minio.service.get_storage(), manifest)


async def request_cancel(book_id: str, run_id: str) -> dict[str, Any]:
    manifest = await read_run(book_id, run_id)
    if manifest is None:
        raise FileNotFoundError(f"Distill run {run_id} was not found")
    if manifest["status"] in {"queued", "running"}:
        await progress.request_cancel(book_id, run_id)
        await progress.emit(book_id, run_id, "run_cancel_requested", status = manifest["status"])
    return {"book_id": book_id, "run_id": run_id, "status": manifest["status"]}


async def mark_failed(book_id: str, run_id: str, error: Exception) -> dict[str, Any] | None:
    manifest = await read_run(book_id, run_id)
    if manifest is None or manifest["status"] in {"done", "failed", "cancelled"}:
        return manifest
    manifest["status"] = "failed"
    manifest["finished_at"] = max(time.time(), manifest["created_at"])
    manifest["error"] = f"Distill failed ({type(error).__name__}). Check worker logs for details."
    manifest["progress"]["current_node"] = None
    await _write_manifest(infra.minio.service.get_storage(), manifest)
    await progress.emit(book_id, run_id, "run_failed", status = manifest["status"], error = manifest["error"])
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
    if manifest["status"] == "done":
        return manifest
    if manifest["status"] not in {"queued", "running"}:
        raise ValueError("only queued or running Distill runs can execute")

    source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if source_manifest is None or source_manifest.get("extraction_id") != manifest["extraction_run_id"]:
        raise ValueError("the book extraction changed after this Distill run was created")
    if _canonical_sha256(source_manifest) != manifest["source_manifest_sha256"]:
        raise ValueError("the source manifest changed after this Distill run was created")
    qa_content = await storage.read_text(manifest["ingestion_ai_manifest_key"])
    if hashlib.sha256(qa_content.encode("utf-8")).hexdigest() != manifest["ingestion_ai_manifest_sha256"]:
        raise ValueError("the ingestion QA manifest changed after this Distill run was created")

    manifest["status"] = "running"
    manifest["error"] = None
    manifest["finished_at"] = None
    await _write_manifest(storage, manifest)
    await progress.emit(book_id, run_id, "run_started", status = "running", progress = manifest["progress"])
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
        if should_check_cancel and await progress.cancel_requested(cancel_redis, book_id, run_id):
            cancellation_observed = True
            raise asyncio.CancelledError
        async with progress_lock:
            current = manifest["progress"]
            if kind == "node":
                node = event.get("node")
                if isinstance(node, str) and node in current["node_status"] and status in {"running", "done", "failed"}:
                    current["node_status"][node] = status
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
            await progress.emit(book_id, run_id, kind if isinstance(kind, str) else "progress", **{
                key: value for key, value in event.items() if key != "kind"
            }, progress = manifest["progress"])

    try:
        extractor = ai.bd.agents.distill.learning_extractor.agent.learning_extractor()
        dependencies = ai.bd.graphs.distill.entities.DistillDependencies(
            storage = storage,
            extractor = extractor,
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
            "expected_page_count": manifest["expected_page_count"],
            "run_created_at": manifest["created_at"],
            "completed_units": [],
            "completed_chapters": [],
        }
        graph_config = {
            "configurable": {"thread_id": f"bd-distill:{book_id}:{manifest['extraction_run_id']}:{run_id}"},
            "max_concurrency": params.GRAPH_CONCURRENCY,
        }
        if checkpointer is not None:
            snapshot = await graph.aget_state(graph_config)
            graph_input = None if snapshot.next else initial_state
        else:
            graph_input = initial_state
        result = await graph.ainvoke(graph_input, config = graph_config)
        required = ("report_key", "report_sha256", "html_key", "html_sha256")
        if any(not isinstance(result.get(field), str) for field in required):
            raise ValueError("the completed graph did not publish every required report artifact")
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
        await progress.emit(book_id, run_id, "run_done", status = "done", progress = manifest["progress"])
        return manifest
    except (asyncio.CancelledError, NodeCancelledError):
        if not cancellation_observed:
            raise
        manifest["status"] = "cancelled"
        manifest["finished_at"] = max(time.time(), manifest["created_at"])
        manifest["error"] = None
        manifest["progress"]["current_node"] = None
        manifest = await _write_manifest(storage, manifest)
        await progress.emit(book_id, run_id, "run_cancelled", status = "cancelled", progress = manifest["progress"])
        return manifest
    except Exception as error:
        manifest["status"] = "failed"
        manifest["finished_at"] = max(time.time(), manifest["created_at"])
        manifest["error"] = f"Distill failed ({type(error).__name__}). Check worker logs for details."
        manifest["progress"]["current_node"] = None
        manifest = await _write_manifest(storage, manifest)
        logger.exception("[bd-distill] run failed book=%s run=%s", book_id, run_id)
        await domains.settings.llm_counter.service.snapshot("bd", run_id)
        await progress.emit(book_id, run_id, "run_failed", status = "failed", error = manifest["error"])
        raise
    finally:
        with contextlib.suppress(Exception):
            await cancel_redis.aclose()
