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

from langgraph.types import Overwrite

import ai
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


def _normalize_progress(manifest: dict[str, Any]) -> dict[str, Any]:
    """Add current graph stages when reading or resuming manifests from an older workflow."""
    progress_state = manifest.setdefault("progress", {})
    node_status = progress_state.setdefault("node_status", {})
    node_status.pop("generate_chapter_illustration", None)
    old_status = dict(node_status)
    for node in params.GRAPH_NODES:
        node_status.setdefault(node, "pending")
    if "verify_unit" not in old_status and old_status.get("extract_unit") == "done":
        node_status["verify_unit"] = "done"
        progress_state["completed_extractions"] = max(
            progress_state.get("completed_extractions", 0),
            progress_state.get("completed_units", 0),
        )
    if "build_chapter_visuals" not in old_status and old_status.get("synthesize_chapter") == "done":
        node_status["build_chapter_visuals"] = "done"
        progress_state["completed_chapter_guides"] = max(
            progress_state.get("completed_chapter_guides", 0),
            progress_state.get("completed_chapters", 0),
        )
        progress_state["completed_chapter_visuals"] = max(
            progress_state.get("completed_chapter_visuals", 0),
            progress_state.get("completed_chapters", 0),
        )
    if "write_decision_brief" not in old_status and old_status.get("synthesize_book") == "done":
        node_status["write_decision_brief"] = "done"
    if "research_context" not in old_status and old_status.get("synthesize_book") == "done":
        node_status["research_context"] = "done"
    progress_state.setdefault("current_task", None)
    progress_state.setdefault("active_tasks", {})
    progress_state.setdefault("node_progress", {})
    for field in (
        "completed_extractions",
        "completed_units",
        "total_units",
        "completed_chapter_guides",
        "completed_chapter_visuals",
        "completed_chapters",
        "total_chapters",
    ):
        progress_state.setdefault(field, 0)
    return progress_state


def _clear_active_node_progress(progress_state: dict[str, Any]) -> None:
    """Clear in-flight counts when a run reaches a terminal state."""
    for node_progress in progress_state.get("node_progress", {}).values():
        if isinstance(node_progress, dict):
            node_progress["active_count"] = 0


def _mark_cancelled(manifest: dict[str, Any]) -> None:
    manifest["status"] = "cancelled"
    manifest["finished_at"] = max(time.time(), manifest["created_at"])
    manifest["error"] = None
    manifest["progress"]["current_node"] = None
    manifest["progress"]["current_task"] = None
    manifest["progress"]["active_tasks"] = {}
    _clear_active_node_progress(manifest["progress"])


async def _persisted_artifact_ids(
    storage: infra.minio.service.MinIOStorage,
    prefix: str,
) -> dict[str, set[str]]:
    candidates = await storage.list(prefix + "extraction-candidates/")
    units = await storage.list(prefix + "learning-units/")
    chapter_keys = await storage.list(prefix + "chapters/")
    guide_ids: set[str] = set()
    visual_ids: set[str] = set()
    for key in chapter_keys:
        chapter_id = key.rsplit("/", 1)[-1].removesuffix(".json")
        guide_ids.add(chapter_id)
        try:
            artifact = json.loads(await storage.read_text(key))
        except json.JSONDecodeError:
            # Let the owning graph stage report corrupt artifacts through normal run failure handling.
            continue
        if not isinstance(artifact, dict):
            continue
        # Missing status means an artifact from the previous combined synthesis-and-visuals stage.
        if artifact.get("visuals_status") in (None, "complete", "unavailable"):
            visual_ids.add(chapter_id)
    return {
        "extractions": {key.rsplit("/", 1)[-1].removesuffix(".json") for key in candidates},
        "units": {key.rsplit("/", 1)[-1].removesuffix(".json") for key in units},
        "chapter_guides": guide_ids,
        "chapter_visuals": visual_ids,
    }


async def _reset_chapter_artifacts(
    storage: infra.minio.service.MinIOStorage,
    prefix: str,
    *,
    clear_coverage_review: bool = False,
) -> None:
    """Reset visual outputs and, when replaying coverage, its saved review checkpoint."""
    for key in await storage.list(prefix + "chapters/"):
        artifact = json.loads(await storage.read_text(key))
        synthesis = artifact.get("synthesis")
        if not isinstance(synthesis, dict):
            raise TypeError("a persisted chapter guide has no synthesis object")
        for field in ("diagrams", "equations", "charts", "timelines", "maps", "source_figures"):
            synthesis[field] = []
        artifact.update({
            "synthesis": synthesis,
            "visual_review_flags": [],
            "visuals_status": "pending",
        })
        if clear_coverage_review:
            for field in (
                "coverage_review_status",
                "coverage_review_notes",
                "coverage_review_input_sha256",
            ):
                artifact.pop(field, None)
        await storage.write(
            key,
            domains.bd.distill.domain.json_body(artifact),
            content_type = params.JSON_TYPE,
        )


async def _reset_chapter_visuals(storage: infra.minio.service.MinIOStorage, prefix: str) -> None:
    """Clear only visual fields so replaying that stage retains chapter guides and review results."""
    await _reset_chapter_artifacts(storage, prefix)


async def _checkpoint_before_replay_node(
    graph: Any,
    graph_config: dict[str, Any],
    node_id: str,
) -> tuple[dict[str, Any], str]:
    """Replay new split stages from the closest old stage when older checkpoints lack their node ID."""
    try:
        return await infra.postgres.service.checkpoint_before_node(graph, graph_config, node_id), node_id
    except ValueError as error:
        fallback_nodes = {
            "verify_unit": "extract_unit",
            "review_chapter_coverage": "synthesize_chapter",
            "build_chapter_visuals": "synthesize_chapter",
            params.PARALLEL_DISPATCH_NODE: "review_chapter_coverage",
            "write_decision_brief": "synthesize_book",
            "research_context": "synthesize_book",
        }
        fallback = fallback_nodes.get(node_id)
        if fallback is None:
            raise
        try:
            return await infra.postgres.service.checkpoint_before_node(graph, graph_config, fallback), fallback
        except ValueError:
            raise error


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
    data = manifest.model_dump(mode = "json")
    _normalize_progress(data)
    return data


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
    source = await domains.bd.ingestion.source.storage.service.read_manifest(storage, book_id)
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
        source_manifest = await domains.bd.ingestion.source.storage.service.read_manifest(storage, book_id)
        if source_manifest is None or source_manifest.get("status") != domains.bd.ingestion.source.storage.params.STATUS_DONE:
            raise ValueError("book extraction must be complete before Distill can start")
        extraction_run_id = source_manifest.get("extraction_id")
        if not _safe_segment(extraction_run_id):
            raise ValueError("the completed extraction has no valid run ID")
        source_manifest_key = domains.bd.ingestion.source.storage.keys.manifest_key(book_id)
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
        qa_manifest_key = domains.bd.ingestion.source.storage.keys.agentic_manifest_key(book_id, qa_run_id)
        qa_manifest_content = await storage.read_text(qa_manifest_key)
        qa_manifest = domains.bd.ingestion.ai.runs.schemas.IngestionAIRunManifest.model_validate_json(
            qa_manifest_content,
        ).model_dump(mode = "json")
        if qa_manifest.get("status") != domains.bd.ingestion.ai.runs.schemas.AgenticRunStatus.DONE.value:
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
        processed, processed_book_key, processed_book_sha256 = (
            await domains.bd.ingestion.ai.processed_book.service.ensure_for_selection(
                book_id,
                qa_run_id,
                selection,
                selection_key,
                selection_sha256,
                ingestion_ai_run = qa_manifest,
                storage = storage,
            )
        )
        await domains.bd.ingestion.ai.processed_book.service.write_current_pointer(
            book_id,
            qa_run_id,
            processed.selection_revision,
            processed_book_key,
            processed_book_sha256,
            storage,
        )
        selected_page_count = processed.source_page_count
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
            processed_book_key = processed_book_key,
            processed_book_sha256 = processed_book_sha256,
            processed_book_revision = processed.selection_revision,
            processed_page_count = processed.source_page_count,
            research_enabled = params.RESEARCH_ENABLED,
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
        _normalize_progress(manifest)
        if manifest.get("resume_from_node") == "generate_chapter_illustration":
            manifest["resume_from_node"] = "build_chapter_visuals"
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
        manifest["replay_from_node"] = None
        manifest["workflow_version"] = versions.WORKFLOW_VERSION
        manifest["prompt_version"] = versions.PROMPT_VERSION
        manifest["renderer_version"] = versions.RENDERER_VERSION
        manifest["schema_version"] = versions.MANIFEST_SCHEMA_VERSION
        manifest["finished_at"] = None
        manifest["error"] = None
        manifest["updated_at"] = time.time()
        manifest["progress"]["current_node"] = None
        manifest["progress"]["current_task"] = None
        manifest["progress"]["active_tasks"] = {}
        for node, status in manifest["progress"]["node_status"].items():
            if status in {"running", "failed"}:
                manifest["progress"]["node_status"][node] = "pending"
        return await _write_manifest(infra.minio.service.get_storage(), manifest)


async def queue_node_replay(book_id: str, run_id: str, node_id: str) -> dict[str, Any]:
    """Queue a node replay, expanding parallel report stages to their shared graph checkpoint."""
    if node_id not in params.GRAPH_NODES:
        raise ValueError(f"unknown Distill node {node_id!r}")
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
        if manifest["status"] in {"queued", "running"}:
            raise ValueError("stop the Distill run before replaying a node")
        active_runs = [run for run in await list_runs(book_id, storage = storage) if run["status"] in {"queued", "running"}]
        if active_runs:
            raise ValueError("stop the active Distill run before replaying a node")
        progress_state = _normalize_progress(manifest)
        node_status = progress_state["node_status"]
        predecessors = set(params.REPLAY_PREDECESSORS[node_id])
        incomplete_predecessors = [
            predecessor
            for predecessor in params.GRAPH_NODES
            if predecessor in predecessors and node_status.get(predecessor) != "done"
        ]
        if incomplete_predecessors:
            predecessor = incomplete_predecessors[0]
            raise ValueError(
                f"Cannot replay {node_id} yet: {predecessor} has not completed. "
                f"Resume the run from its saved checkpoint or replay {predecessor}."
            )

        checkpoint_node_id = (
            params.PARALLEL_DISPATCH_NODE
            if node_id in params.PARALLEL_REPORT_REPLAY_NODES
            else node_id
        )
        replay_output_nodes = set(params.REPLAY_DESCENDANTS[node_id])
        replay_execution_nodes = set(params.REPLAY_DESCENDANTS[node_id])

        checkpointer = infra.postgres.service.get_checkpointer()
        graph = ai.bd.graphs.distill.graphs.distill_graph(checkpointer = checkpointer)
        graph_config = {"configurable": {
            "thread_id": f"bd-distill:{book_id}:{manifest['extraction_run_id']}:{run_id}",
        }}
        _, resolved_checkpoint_node_id = await _checkpoint_before_replay_node(
            graph,
            graph_config,
            checkpoint_node_id,
        )
        if node_id in params.PARALLEL_REPORT_REPLAY_NODES:
            replay_execution_nodes = set(params.REPLAY_DESCENDANTS[node_id])
            if resolved_checkpoint_node_id == "review_chapter_coverage":
                replay_execution_nodes.add("review_chapter_coverage")
            elif resolved_checkpoint_node_id == "synthesize_chapter":
                replay_execution_nodes.update({"synthesize_chapter", "review_chapter_coverage"})
        elif resolved_checkpoint_node_id in params.REPLAY_DESCENDANTS:
            replay_execution_nodes = set(params.REPLAY_DESCENDANTS[resolved_checkpoint_node_id])
        await progress.service.clear_cancel(book_id, run_id)

        prefix = keys.run_prefix(book_id, run_id)
        if node_id in {"prepare_units", "extract_unit"}:
            await storage.delete_prefix(prefix + "extraction-candidates/")
            await storage.delete_prefix(prefix + "learning-units/")
        elif node_id == "verify_unit":
            await storage.delete_prefix(prefix + "learning-units/")
        if node_id in {
            "prepare_units",
            "extract_unit",
            "verify_unit",
            "prepare_chapters",
            "synthesize_chapter",
        }:
            await storage.delete_prefix(prefix + "chapters/")
        elif node_id == "build_chapter_visuals":
            await _reset_chapter_visuals(storage, prefix)
        elif node_id == "review_chapter_coverage":
            await _reset_chapter_artifacts(storage, prefix, clear_coverage_review = True)
        if "synthesize_book" in replay_output_nodes:
            await storage.delete_prefix(prefix + "synthesis/")
        else:
            if "research_context" in replay_output_nodes:
                await storage.delete_prefix(prefix + "synthesis/research-context.json")
            if "write_decision_brief" in replay_output_nodes:
                await storage.delete_prefix(prefix + "synthesis/decision-brief.json")
        await storage.delete_prefix(prefix + "report.json")
        await storage.delete_prefix(prefix + "report.html")
        await storage.delete_prefix(prefix + "report.pdf")

        retained_llm_nodes = {
            usage_node
            for graph_node in params.GRAPH_NODES
            if graph_node not in replay_output_nodes
            for usage_node in params.LLM_COUNTER_NODES_BY_GRAPH_NODE[graph_node]
        }
        await domains.settings.llm_counter.service.retain_nodes("bd", run_id, retained_llm_nodes)

        downstream = replay_execution_nodes
        for downstream_node in downstream:
            progress_state["node_status"][downstream_node] = "pending"
        progress_state["current_node"] = None
        progress_state["current_task"] = None
        progress_state["active_tasks"] = {}
        for downstream_node in downstream | {resolved_checkpoint_node_id}:
            progress_state.setdefault("node_progress", {}).pop(downstream_node, None)
        artifact_ids = await _persisted_artifact_ids(storage, prefix)
        progress_state["completed_extractions"] = len(artifact_ids["extractions"])
        progress_state["completed_units"] = len(artifact_ids["units"])
        progress_state["completed_chapter_guides"] = len(artifact_ids["chapter_guides"])
        progress_state["completed_chapter_visuals"] = len(artifact_ids["chapter_visuals"])
        progress_state["completed_chapters"] = len(artifact_ids["chapter_visuals"])
        manifest.update(
            status = "queued",
            schema_version = versions.MANIFEST_SCHEMA_VERSION,
            workflow_version = versions.WORKFLOW_VERSION,
            prompt_version = versions.PROMPT_VERSION,
            renderer_version = versions.RENDERER_VERSION,
            resume_from_node = None,
            replay_from_node = resolved_checkpoint_node_id,
            replay_target_node = node_id,
            finished_at = None,
            error = None,
            report_key = None,
            report_sha256 = None,
            html_key = None,
            html_sha256 = None,
            pdf_key = None,
            pdf_sha256 = None,
            covered_page_count = 0,
            uncovered_pages = [],
            review_required = True,
            structural_quality = None,
        )
        manifest = await _write_manifest(storage, manifest)
        await progress.service.clear(book_id, run_id)
        await progress.service.emit(
            book_id,
            run_id,
            "run_queued",
            status = "queued",
            progress = manifest["progress"],
            replay_from_node = checkpoint_node_id,
        )
        return manifest


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
            _mark_cancelled(manifest)
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
            task_id = manifest.get("celery_task_id")
            if isinstance(task_id, str) and task_id:
                try:
                    await asyncio.to_thread(infra.celery.service.revoke, task_id)
                except Exception as error:
                    logger.warning(
                        "[bd-distill] unable to revoke Celery task %s (%s: %s)",
                        task_id,
                        type(error).__name__,
                        error,
                    )
            task_state = await asyncio.to_thread(
                infra.celery.service.inspect_task_by_args,
                params.TASK_NAME,
                (book_id, run_id),
            )
            current = await read_run(book_id, run_id, storage = storage)
            if current is None:
                raise FileNotFoundError(f"Distill run {run_id} was not found")
            manifest = current
            if manifest["status"] == "running" and task_state == "absent":
                logger.warning(
                    "[bd-distill] finalizing cancellation for orphaned run book=%s run=%s",
                    book_id,
                    run_id,
                )
                _mark_cancelled(manifest)
                manifest = await _write_manifest(storage, manifest)
                await progress.service.emit(
                    book_id,
                    run_id,
                    "run_cancelled",
                    status = "cancelled",
                    progress = manifest["progress"],
                )
            elif manifest["status"] == "running":
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
        ):
            manifest = await read_run(book_id, run_id, storage = storage)
            if manifest is None:
                raise FileNotFoundError(f"Distill run {run_id} was not found")
            if manifest["status"] in {"queued", "running"}:
                raise ValueError("stop the Distill run and wait for it to finish before wiping it")
            runs = await list_runs(book_id, storage = storage)
            if any(run["status"] in {"queued", "running"} for run in runs):
                raise ValueError("stop the active Distill run before wiping another run")
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
    manifest["progress"]["current_task"] = None
    manifest["progress"]["active_tasks"] = {}
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
    storage: Any,
    initial_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Rehydrate chapter-stage state from durable source and lesson artifacts."""
    prepared = await domains.bd.distill.extract_learning_units.service.prepare_units(
        storage,
        initial_state,
    )
    completed_units = []
    for ref in prepared.get("unit_refs", []):
        artifact_key = keys.learning_unit_key(
            initial_state["book_id"], initial_state["run_id"], ref["unit_id"],
        )
        if not await storage.exists(artifact_key):
            return None
        artifact = json.loads(await storage.read_text(artifact_key))
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


async def mark_run_started(
    book_id: str,
    run_id: str,
    celery_task_id: str | None = None,
) -> dict[str, Any]:
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
        was_queued = manifest["status"] == "queued"
        task_id_changed = celery_task_id is not None and manifest.get("celery_task_id") != celery_task_id
        if task_id_changed:
            manifest["celery_task_id"] = celery_task_id
        if was_queued:
            manifest["status"] = "running"
            manifest["error"] = None
            manifest["finished_at"] = None
        if task_id_changed or was_queued:
            manifest = await _write_manifest(storage, manifest)
        if was_queued:
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

    report_data, _ = await read_report(book_id, run_id)
    report = schemas.StudyGuideDocument.model_validate(report_data)
    source_figure_data = {}
    total_figure_bytes = 0
    for chapter in report.chapters:
        for figure in chapter.source_figures:
            if total_figure_bytes >= domains.bd.distill.rendering.params.MAX_INLINE_SOURCE_FIGURE_TOTAL_BYTES:
                continue
            try:
                image = await domains.bd.ingestion.source.storage.service.read_figure(
                    storage,
                    book_id,
                    figure.figure_name,
                    report.extraction_run_id,
                )
            except Exception:  # noqa: BLE001 — PDF remains readable if an optional source figure disappeared.
                continue
            if (
                image is not None
                and image.startswith(b"\x89PNG\r\n\x1a\n")
                and len(image) <= domains.bd.distill.rendering.params.MAX_INLINE_SOURCE_FIGURE_BYTES
                and total_figure_bytes + len(image)
                <= domains.bd.distill.rendering.params.MAX_INLINE_SOURCE_FIGURE_TOTAL_BYTES
                and hashlib.sha256(image).hexdigest() == figure.image_sha256
            ):
                source_figure_data[figure.figure_name] = image
                total_figure_bytes += len(image)
    pdf_html = domains.bd.distill.rendering.domain.render_html(report, source_figure_data)
    pdf = await domains.bd.distill.rendering.service.render_pdf(pdf_html)
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

    source_manifest = await domains.bd.ingestion.source.storage.service.read_manifest(storage, book_id)
    if source_manifest is None or source_manifest.get("extraction_id") != manifest["extraction_run_id"]:
        raise ValueError("the book extraction changed after this Distill run was created")
    if _canonical_sha256(source_manifest) != manifest["source_manifest_sha256"]:
        raise ValueError("the source manifest changed after this Distill run was created")
    qa_content = await storage.read_text(manifest["ingestion_ai_manifest_key"])
    if hashlib.sha256(qa_content.encode("utf-8")).hexdigest() != manifest["ingestion_ai_manifest_sha256"]:
        raise ValueError("the ingestion QA manifest changed after this Distill run was created")
    if manifest.get("schema_version") in {"1.1", "1.2", "1.3", "1.4", "1.5", "1.6"}:
        selection_content = await storage.read_text(manifest["content_selection_key"])
        if hashlib.sha256(selection_content.encode("utf-8")).hexdigest() != manifest["content_selection_sha256"]:
            raise ValueError("the content-selection revision changed after this Distill run was created")
        selection = domains.bd.ingestion.ai.content_selection.schemas.ContentSelectionManifest.model_validate_json(selection_content)
        if (
            selection.book_id != book_id
            or selection.extraction_run_id != manifest["extraction_run_id"]
            or selection.agentic_run_id != manifest["ingestion_ai_run_id"]
            or selection.revision != manifest["content_selection_revision"]
            or (
                manifest.get("schema_version") == "1.1"
                and sum(page.disposition == "include" for page in selection.pages) != manifest["selected_page_count"]
            )
        ):
            raise ValueError("the pinned content-selection revision does not match this Distill run")
    if manifest.get("schema_version") in {"1.2", "1.3", "1.4", "1.5", "1.6"}:
        processed_content = await storage.read_text(manifest["processed_book_key"])
        if hashlib.sha256(processed_content.encode("utf-8")).hexdigest() != manifest["processed_book_sha256"]:
            raise ValueError("the pinned processed-book manifest failed its integrity check")
        processed = domains.bd.ingestion.ai.processed_book.schemas.ProcessedBookManifest.model_validate_json(processed_content)
        if (
            processed.book_id != book_id
            or processed.agentic_run_id != manifest["ingestion_ai_run_id"]
            or processed.extraction_run_id != manifest["extraction_run_id"]
            or processed.selection_revision != manifest["processed_book_revision"]
            or processed.source_page_count != manifest["processed_page_count"]
            or processed.selection_key != manifest["content_selection_key"]
            or processed.selection_sha256 != manifest["content_selection_sha256"]
        ):
            raise ValueError("the pinned processed-book manifest does not match this Distill run")

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

    artifact_ids = await _persisted_artifact_ids(storage, keys.run_prefix(book_id, run_id))
    extracted_unit_ids = artifact_ids["extractions"]
    completed_unit_ids = artifact_ids["units"]
    completed_chapter_guide_ids = artifact_ids["chapter_guides"]
    completed_chapter_visual_ids = artifact_ids["chapter_visuals"]
    completed_chapter_ids = set(completed_chapter_visual_ids)
    progress_state = _normalize_progress(manifest)
    progress_state.update(
        completed_extractions = len(extracted_unit_ids),
        completed_units = len(completed_unit_ids),
        completed_chapter_guides = len(completed_chapter_guide_ids),
        completed_chapter_visuals = len(completed_chapter_visual_ids),
        completed_chapters = len(completed_chapter_visual_ids),
    )
    node_work_completed: dict[str, set[str]] = {
        "extract_unit": set(extracted_unit_ids),
        "verify_unit": set(completed_unit_ids),
        "synthesize_chapter": set(completed_chapter_guide_ids),
        "build_chapter_visuals": set(completed_chapter_visual_ids),
    }
    node_work_active: dict[str, set[str]] = {}
    node_work_totals: dict[str, int] = {}

    def update_node_progress(node: str, total_count: int | None = None) -> None:
        if total_count is not None:
            node_work_totals[node] = total_count
        total = node_work_totals.get(node, 0)
        current_progress = manifest["progress"].setdefault("node_progress", {})
        current_progress[node] = {
            "completed_count": min(len(node_work_completed.get(node, set())), total) if total else 0,
            "total_count": total,
            "active_count": len(node_work_active.get(node, set())),
        }

    for node, total in (
        ("extract_unit", progress_state["total_units"]),
        ("verify_unit", progress_state["total_units"]),
        ("synthesize_chapter", progress_state["total_chapters"]),
        ("review_chapter_coverage", progress_state["total_chapters"]),
        ("build_chapter_visuals", progress_state["total_chapters"]),
    ):
        update_node_progress(node, total)
    manifest = await _write_manifest(storage, manifest)
    progress_lock = asyncio.Lock()
    cancellation_observed = False
    cancel_redis = infra.redis.service.new_redis()

    async def report_progress(event: dict[str, Any]) -> None:
        nonlocal cancellation_observed, manifest
        kind = event.get("kind")
        status = event.get("status")
        should_check_cancel = (kind == "node" and status == "running") or (
            kind == "activity" and isinstance(event.get("current_task"), str)
        ) or (kind == "node_item_progress" and status == "running") or kind in {
            "unit",
            "chapter",
            "unit_extraction",
            "unit_verification",
            "chapter_guide",
            "chapter_visuals",
        }
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
                    if status == "running":
                        current["current_node"] = node
                        current["current_task"] = None
                        current.setdefault("active_tasks", {}).pop(node, None)
                    else:
                        node_work_active.setdefault(node, set()).clear()
                        update_node_progress(node)
                        active_tasks = current.setdefault("active_tasks", {})
                        active_tasks.pop(node, None)
                        if current.get("current_node") == node:
                            focus_node = next(reversed(active_tasks), None) if active_tasks else None
                            current["current_node"] = focus_node
                            current["current_task"] = active_tasks.get(focus_node) if focus_node else None
            elif kind == "activity":
                task = event.get("current_task")
                node = event.get("node")
                if isinstance(task, str) and isinstance(node, str) and node in current["node_status"]:
                    active_tasks = current.setdefault("active_tasks", {})
                    active_tasks[node] = task[:240]
                    current["current_node"] = node
                    current["current_task"] = task[:240]
            elif kind == "node_work_plan":
                node = event.get("node")
                total_count = event.get("total_count")
                if (
                    isinstance(node, str)
                    and node in current["node_status"]
                    and type(total_count) is int
                    and total_count >= 0
                ):
                    update_node_progress(node, total_count)
            elif kind == "node_item_progress":
                node = event.get("node")
                item_id = event.get("item_id")
                total_count = event.get("total_count")
                if isinstance(node, str) and node in current["node_status"] and isinstance(item_id, str):
                    if type(total_count) is int and total_count >= 0:
                        node_work_totals[node] = total_count
                    active_items = node_work_active.setdefault(node, set())
                    completed_items = node_work_completed.setdefault(node, set())
                    if status == "running":
                        completed_items.discard(item_id)
                        active_items.add(item_id)
                    elif status == "done":
                        active_items.discard(item_id)
                        completed_items.add(item_id)
                    elif status in {"failed", "cancelled"}:
                        active_items.discard(item_id)
                    update_node_progress(node)
            elif kind == "node_page_progress":
                node = event.get("node")
                completed_count = event.get("completed_count")
                total_count = event.get("total_count")
                active_count = event.get("active_count")
                if (
                    isinstance(node, str)
                    and node in current["node_status"]
                    and all(type(value) is int and value >= 0 for value in (completed_count, total_count, active_count))
                ):
                    current.setdefault("node_progress", {})[node] = {
                        "completed_count": min(completed_count, total_count),
                        "total_count": total_count,
                        "active_count": active_count,
                    }
            elif kind == "unit_plan":
                current["total_units"] = int(event.get("total_units", 0))
                update_node_progress("extract_unit", current["total_units"])
                update_node_progress("verify_unit", current["total_units"])
            elif kind == "unit_extraction" and status == "done":
                unit_id = event.get("unit_id")
                if isinstance(unit_id, str):
                    extracted_unit_ids.add(unit_id)
                current["completed_extractions"] = len(extracted_unit_ids)
            elif kind in {"unit_verification", "unit"} and status == "done":
                unit_id = event.get("unit_id")
                if isinstance(unit_id, str):
                    completed_unit_ids.add(unit_id)
                current["completed_units"] = len(completed_unit_ids)
            elif kind == "chapter_plan":
                current["total_chapters"] = int(event.get("total_chapters", 0))
                update_node_progress("synthesize_chapter", current["total_chapters"])
                update_node_progress("review_chapter_coverage", current["total_chapters"])
                update_node_progress("build_chapter_visuals", current["total_chapters"])
            elif kind == "chapter_guide" and status == "done":
                chapter_id = event.get("chapter_id")
                if isinstance(chapter_id, str):
                    completed_chapter_guide_ids.add(chapter_id)
                current["completed_chapter_guides"] = len(completed_chapter_guide_ids)
            elif kind == "chapter_visuals" and status == "done":
                chapter_id = event.get("chapter_id")
                if isinstance(chapter_id, str):
                    completed_chapter_visual_ids.add(chapter_id)
                current["completed_chapter_visuals"] = len(completed_chapter_visual_ids)
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
        learning_extractor = ai.bd.agents.distill.learning_extractor.agent.learning_extractor()
        research_search_session = ai.bd.tools.search_mcp.entities.SearchSession(run_id = run_id)
        dependencies = ai.bd.graphs.distill.entities.DistillDependencies(
            storage = storage,
            learning_extractor = learning_extractor,
            evidence_verifier = ai.bd.agents.distill.evidence_verifier.agent.evidence_verifier(),
            chapter_synthesizer = ai.bd.agents.distill.chapter_synthesizer.agent.chapter_synthesizer(),
            visual_artifact_builder = ai.bd.agents.distill.visual_artifact_builder.agent.visual_artifact_builder(),
            book_synthesizer = ai.bd.agents.distill.book_synthesizer.agent.book_synthesizer(),
            decision_brief_writer = ai.bd.agents.distill.decision_brief_writer.agent.decision_brief_writer(),
            research_analyst = ai.bd.agents.distill.research_analyst.agent.research_analyst(),
            research_search_session = research_search_session,
            report_reviewer = ai.bd.agents.distill.report_reviewer.agent.report_reviewer(),
            controls = ai.bd.graphs.distill.entities.DistillRunControls(
                chapter_synthesis_semaphore = asyncio.Semaphore(
                    params.CHAPTER_SYNTHESIS_CONCURRENCY,
                ),
            ),
            progress_callback = report_progress,
            research_enabled = bool(manifest.get("research_enabled", False)),
        )
        graph = ai.bd.graphs.distill.graphs.distill_graph(checkpointer = checkpointer)
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
            "processed_book_key": manifest.get("processed_book_key"),
            "processed_book_sha256": manifest.get("processed_book_sha256"),
            "processed_book_revision": manifest.get("processed_book_revision", 1),
            "processed_page_count": manifest.get(
                "processed_page_count",
                manifest.get("selected_page_count", manifest["expected_page_count"]),
            ),
            "expected_page_count": manifest["expected_page_count"],
            "selected_page_count": manifest.get("selected_page_count", manifest["expected_page_count"]),
            "run_created_at": manifest["created_at"],
            "resume_from_node": manifest.get("resume_from_node"),
            "extracted_units": [],
            "completed_units": [],
            "synthesized_chapters": [],
            "reviewed_chapters": [],
            "completed_chapters": [],
            "decision_brief_key": None,
        }
        graph_config = {
            "configurable": {
                "thread_id": f"bd-distill:{book_id}:{manifest['extraction_run_id']}:{run_id}",
                **(
                    {"distill_replay_target": manifest["replay_target_node"]}
                    if manifest.get("replay_target_node")
                    else {}
                ),
            },
            "max_concurrency": params.GRAPH_CONCURRENCY,
        }
        replay_from_node = manifest.get("replay_from_node")
        completed_checkpoint_state = None
        if checkpointer is not None:
            if replay_from_node in params.GRAPH_NODES or replay_from_node == params.PARALLEL_DISPATCH_NODE:
                replay_config, resolved_checkpoint_node_id = await _checkpoint_before_replay_node(
                    graph,
                    graph_config,
                    replay_from_node,
                )
                replay_target_node = manifest.get("replay_target_node") or replay_from_node
                reset_patch: dict[str, Any] = {
                    "report_key": None,
                    "report_sha256": None,
                    "html_key": None,
                    "html_sha256": None,
                    "covered_page_count": 0,
                    "uncovered_pages": [],
                    "review_required": True,
                    "structural_quality": None,
                }
                replay_descendants = set(params.REPLAY_DESCENDANTS.get(replay_target_node, ()))
                if "synthesize_book" in replay_descendants:
                    reset_patch["book_synthesis_key"] = None
                if "write_decision_brief" in replay_descendants:
                    reset_patch["decision_brief_key"] = None
                if "research_context" in replay_descendants:
                    reset_patch["research_context_key"] = None
                if resolved_checkpoint_node_id == "prepare_units":
                    reset_patch.update(
                        unit_refs = [],
                        extracted_units = Overwrite([]),
                        completed_units = Overwrite([]),
                        chapter_refs = [],
                        synthesized_chapters = Overwrite([]),
                        reviewed_chapters = Overwrite([]),
                        completed_chapters = Overwrite([]),
                    )
                elif resolved_checkpoint_node_id == "extract_unit":
                    reset_patch.update(
                        extracted_units = Overwrite([]),
                        completed_units = Overwrite([]),
                        chapter_refs = [],
                        synthesized_chapters = Overwrite([]),
                        reviewed_chapters = Overwrite([]),
                        completed_chapters = Overwrite([]),
                    )
                elif resolved_checkpoint_node_id == "verify_unit":
                    reset_patch.update(
                        completed_units = Overwrite([]),
                        chapter_refs = [],
                        synthesized_chapters = Overwrite([]),
                        reviewed_chapters = Overwrite([]),
                        completed_chapters = Overwrite([]),
                    )
                elif resolved_checkpoint_node_id == "prepare_chapters":
                    reset_patch.update(
                        chapter_refs = [],
                        synthesized_chapters = Overwrite([]),
                        reviewed_chapters = Overwrite([]),
                        completed_chapters = Overwrite([]),
                    )
                elif resolved_checkpoint_node_id == "synthesize_chapter":
                    reset_patch.update(
                        synthesized_chapters = Overwrite([]),
                        reviewed_chapters = Overwrite([]),
                    )
                    if replay_target_node not in params.REPORT_PIPELINE_NODES:
                        reset_patch["completed_chapters"] = Overwrite([])
                elif resolved_checkpoint_node_id == "review_chapter_coverage":
                    reset_patch["reviewed_chapters"] = Overwrite([])
                    if replay_target_node not in params.REPORT_PIPELINE_NODES:
                        reset_patch["completed_chapters"] = Overwrite([])
                elif replay_target_node == "build_chapter_visuals":
                    reset_patch["completed_chapters"] = Overwrite([])
                replay_config = await graph.aupdate_state(replay_config, reset_patch)
                graph_config = {
                    **graph_config,
                    "configurable": {
                        **graph_config["configurable"],
                        **replay_config["configurable"],
                    },
                }
                graph_input = None
                logger.info(
                    "[bd-distill] replaying graph from node=%s run=%s",
                    replay_from_node,
                    run_id,
                )
            else:
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
        chapter_stage_nodes = {
            "prepare_chapters",
            "synthesize_chapter",
            "review_chapter_coverage",
            "build_chapter_visuals",
            "synthesize_book",
            "write_decision_brief",
            "research_context",
            "publish_report",
        }
        if graph_input is not None and resume_from_node in chapter_stage_nodes:
            restored_state = await _restore_chapter_resume_state(storage, initial_state)
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
            result = await ai.bd.nodes.distill.publish_report.node.publish_report_with_dependencies(
                dependencies,
                completed_checkpoint_state,
            )
        else:
            result = await graph.ainvoke(
                graph_input,
                config = graph_config,
                context = dependencies,
            )
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
            replay_from_node = None,
            replay_target_node = None,
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
        _mark_cancelled(manifest)
        manifest = await _write_manifest(storage, manifest)
        await progress.service.emit(book_id, run_id, "run_cancelled", status = "cancelled", progress = manifest["progress"])
        return manifest
    except Exception as error:
        manifest["status"] = "failed"
        manifest["finished_at"] = max(time.time(), manifest["created_at"])
        manifest["error"] = _failure_message(error)
        manifest["progress"]["current_node"] = None
        manifest["progress"]["current_task"] = None
        manifest["progress"]["active_tasks"] = {}
        _clear_active_node_progress(manifest["progress"])
        manifest = await _write_manifest(storage, manifest)
        logger.exception("[bd-distill] run failed book=%s run=%s", book_id, run_id)
        await domains.settings.llm_counter.service.snapshot("bd", run_id)
        await progress.service.emit(book_id, run_id, "run_failed", status = "failed", error = manifest["error"])
        raise
    finally:
        with contextlib.suppress(Exception):
            await cancel_redis.aclose()
