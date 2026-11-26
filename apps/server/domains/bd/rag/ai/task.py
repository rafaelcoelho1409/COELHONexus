"""Celery worker for the independent processed-book retrieval-index graph."""
from __future__ import annotations
import ai
import infra.celery
from . import errors, keys, params, replay, runs
from .. import source
from ... import ingestion
from .... import settings

import asyncio
import contextlib
import logging
import time
import uuid


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = params.TASK_NAME,
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
    max_retries = None,
)
def index_processed_book(self, book_id: str, agentic_run_id: str) -> dict:
    """Build the full-text, vector, and graph indexes from one published page revision."""
    logger.info("[bd-retrieval-index] book_id=%s agentic_run_id=%s", book_id, agentic_run_id)

    async def execute() -> dict:
        storage = infra.minio.service.get_storage()
        owner = f"{self.request.id or agentic_run_id}:{uuid.uuid4().hex}"
        async with infra.redis.service.client() as redis:
            lock_key = keys.lock_key(book_id, agentic_run_id)
            lease_lost = asyncio.Event()
            heartbeat = None
            checkpointer_initialized = False

            async def renew_execution_lease() -> None:
                while True:
                    await asyncio.sleep(params.RUN_LOCK_RENEW_S)
                    try:
                        renewed = await infra.redis.service.extend_if_owner(
                            redis,
                            lock_key,
                            owner,
                            params.RUN_LOCK_LEASE_S,
                        )
                    except Exception:
                        logger.exception("[bd-retrieval-index] unable to renew execution lease for %s", agentic_run_id)
                        lease_lost.set()
                        return
                    if not renewed:
                        lease_lost.set()
                        logger.error("[bd-retrieval-index] execution lease lost for %s", agentic_run_id)
                        return

            def qdrant_will_run(current_manifest: dict) -> bool:
                replay_from_node = current_manifest.get("replay_from_node")
                if replay_from_node:
                    return "qdrant" in params.REPLAY_DESCENDANTS.get(replay_from_node, ())
                return current_manifest.get("node_status", {}).get("qdrant") != "done"

            try:
                async with redis.lock(
                    ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
                    timeout = 120,
                    blocking_timeout = 30,
                ):
                    acquired, current_owner = await infra.redis.service.try_lock(
                        redis,
                        lock_key,
                        owner,
                        params.RUN_LOCK_LEASE_S,
                    )
                    if not acquired:
                        return {"execution_lock_busy": True, "owner": current_owner}
                    manifest = await runs.service.read_index(book_id, agentic_run_id, storage = storage)
                    if manifest is None:
                        raise FileNotFoundError("retrieval-index run manifest was not found")
                    if manifest["status"] in {"done", "cancelled"}:
                        return {
                            "book_id": book_id,
                            "agentic_run_id": agentic_run_id,
                            "status": manifest["status"],
                            "ignored_stale_delivery": True,
                        }
                    if await runs.service.cancel_requested(book_id, agentic_run_id):
                        manifest.update(
                            status = "cancelled",
                            cancel_requested = False,
                            finished_at = max(time.time(), manifest["created_at"]),
                            error = None,
                        )
                        await runs.service.write_manifest(manifest, storage = storage)
                        return {"book_id": book_id, "agentic_run_id": agentic_run_id, "status": "cancelled"}

                    manifest.update(
                        status = "running",
                        updated_at = max(time.time(), manifest["created_at"]),
                        finished_at = None,
                        error = None,
                    )
                    manifest = await runs.service.write_manifest(manifest, storage = storage)
                heartbeat = asyncio.create_task(renew_execution_lease())
                embedding_profile = None
                if qdrant_will_run(manifest):
                    configured_model = settings.embeddings.service.get_configured_model()
                    try:
                        probe, metadata = await settings.embeddings.service.embed_probe_async()
                    except Exception as error:
                        logger.exception(
                            "[bd-retrieval-index] embedding preflight failed for model %s",
                            configured_model,
                        )
                        if getattr(error, "status_code", None) == 400:
                            message = (
                                f"The embedding provider rejected model {configured_model!r}. "
                                "Choose a model available on the configured endpoint, then retry."
                            )
                        else:
                            message = (
                                f"Could not verify embedding model {configured_model!r}. "
                                "Check the configured endpoint, credentials, and model availability, then retry."
                            )
                        raise errors.EmbeddingPreflightError(message) from error

                    dimensions = len(probe)
                    model = str(metadata.get("model") or "").strip()
                    if dimensions < 1 or not model:
                        raise errors.EmbeddingPreflightError(
                            "The embedding endpoint returned no model or vector dimensions. "
                            "Check the configured endpoint, then retry."
                        )
                    embedding_profile = (model, dimensions)
                    manifest.update(
                        embedding_model = model,
                        embedding_dimensions = dimensions,
                    )
                    manifest = await runs.service.write_manifest(manifest, storage = storage)

                await infra.postgres.service.init_checkpointer()
                checkpointer_initialized = True
                graph = ai.bd.graphs.ingestion.rag_indexing.main.graphs.retrieval_index_graph(
                    checkpointer = infra.postgres.service.get_checkpointer(),
                )
                config = {"configurable": {"thread_id": keys.thread_id(
                    book_id,
                    manifest["extraction_run_id"],
                    agentic_run_id,
                    manifest["workflow_version"],
                    manifest["processed_book_revision"],
                    manifest.get("qdrant_index_profile_fingerprint"),
                    manifest.get("checkpoint_generation", 0),
                )}}
                initial_state = {
                    "book_id": book_id,
                    "extraction_run_id": manifest["extraction_run_id"],
                    "agentic_run_id": agentic_run_id,
                    "processed_book_key": manifest["processed_book_key"],
                    "processed_book_sha256": manifest["processed_book_sha256"],
                    "processed_book_revision": manifest["processed_book_revision"],
                    "processed_page_count": manifest["processed_page_count"],
                    "source_artifact_key": manifest.get("source_artifact_key", ""),
                    "pin_sources_pages_completed": manifest.get("pin_sources_pages_completed", 0),
                    "elasticsearch_index": manifest.get("elasticsearch_index", ""),
                    "elasticsearch_document_count": manifest.get("elasticsearch_document_count", 0),
                    "elasticsearch_passage_index": manifest.get("elasticsearch_passage_index", ""),
                    "elasticsearch_passage_count": manifest.get("elasticsearch_passage_count", 0),
                    "elasticsearch_pages_completed": manifest.get("elasticsearch_pages_completed", 0),
                    "qdrant_collection": manifest.get("qdrant_collection", ""),
                    "embedding_model": manifest.get("embedding_model", ""),
                    "embedding_dimensions": manifest.get("embedding_dimensions", 0),
                    "qdrant_point_count": manifest.get("qdrant_point_count", 0),
                    "qdrant_pages_completed": manifest.get("qdrant_pages_completed", 0),
                    "neo4j_node_count": manifest.get("neo4j_node_count", 0),
                    "neo4j_relationship_count": manifest.get("neo4j_relationship_count", 0),
                    "graph_batches_empty_count": manifest.get("graph_batches_empty_count", 0),
                    "graph_batches_entity_only_count": manifest.get("graph_batches_entity_only_count", 0),
                    "graph_extraction_split_count": manifest.get("graph_extraction_split_count", 0),
                    "neo4j_pages_completed": manifest.get("neo4j_pages_completed", 0),
                    "neo4j_stage": manifest.get("neo4j_stage", "idle"),
                    "neo4j_stage_completed": manifest.get("neo4j_stage_completed", 0),
                    "neo4j_stage_total": manifest.get("neo4j_stage_total", 0),
                    "neo4j_node_write_batches_completed": manifest.get(
                        "neo4j_node_write_batches_completed", 0,
                    ),
                    "neo4j_node_write_batches_total": manifest.get("neo4j_node_write_batches_total", 0),
                    "neo4j_relationship_write_batches_completed": manifest.get(
                        "neo4j_relationship_write_batches_completed", 0,
                    ),
                    "neo4j_relationship_write_batches_total": manifest.get(
                        "neo4j_relationship_write_batches_total", 0,
                    ),
                }
                snapshot = await graph.aget_state(config)
                replay_from_node = manifest.get("replay_from_node")
                if replay_from_node:
                    config = await replay.service.prepare_graph_replay(graph, config, replay_from_node)
                    manifest["replay_from_node"] = None
                    manifest = await runs.service.write_manifest(manifest, storage = storage)
                    snapshot = await graph.aget_state(config)
                progress_lock = asyncio.Lock()

                async def ensure_lease() -> None:
                    if lease_lost.is_set():
                        raise RuntimeError("retrieval-index execution lease was lost")
                    current = await redis.get(lock_key)
                    if isinstance(current, bytes):
                        current = current.decode()
                    if current != owner:
                        lease_lost.set()
                        raise RuntimeError("retrieval-index execution lease was lost")

                async def report_progress(node: str, status: str, details: dict | None) -> None:
                    async with progress_lock:
                        await ensure_lease()
                        current = await runs.service.read_index(book_id, agentic_run_id, storage = storage)
                        if current is None:
                            raise FileNotFoundError("retrieval-index manifest disappeared during execution")
                        node_status = current["node_status"]
                        if node not in node_status:
                            raise ValueError(f"unknown retrieval-index graph node {node!r}")
                        node_status[node] = status
                        current["node_status"] = node_status
                        if status == "running" and details is None:
                            page_progress_fields = {
                                "pin_sources": "pin_sources_pages_completed",
                                "elasticsearch": "elasticsearch_pages_completed",
                                "qdrant": "qdrant_pages_completed",
                                "neo4j": "neo4j_pages_completed",
                            }
                            progress_field = page_progress_fields.get(node)
                            if progress_field is not None:
                                current[progress_field] = 0
                            if node == "neo4j":
                                current.update(
                                    neo4j_stage="idle",
                                    neo4j_stage_completed=0,
                                    neo4j_stage_total=0,
                                    neo4j_llm_calls_active=0,
                                    graph_batches_empty_count=0,
                                    graph_batches_entity_only_count=0,
                                    graph_extraction_split_count=0,
                                    neo4j_node_write_batches_completed=0,
                                    neo4j_node_write_batches_total=0,
                                    neo4j_relationship_write_batches_completed=0,
                                    neo4j_relationship_write_batches_total=0,
                                )
                        current["current_node"] = node if status == "running" else (
                            None if current.get("current_node") == node else current.get("current_node")
                        )
                        current["cancel_requested"] = await runs.service.cancel_requested(
                            book_id,
                            agentic_run_id,
                        )
                        if details:
                            for field in (
                                "source_artifact_key",
                                "pin_sources_pages_completed",
                                "elasticsearch_index",
                                "elasticsearch_document_count",
                                "elasticsearch_passage_index",
                                "elasticsearch_passage_count",
                                "elasticsearch_pages_completed",
                                "qdrant_collection",
                                "embedding_model",
                                "embedding_dimensions",
                                "qdrant_point_count",
                                "qdrant_pages_completed",
                                "neo4j_node_count",
                                "neo4j_relationship_count",
                                "graph_batches_completed",
                                "graph_batches_total",
                                "graph_batches_empty_count",
                                "graph_batches_entity_only_count",
                                "graph_extraction_split_count",
                                "neo4j_pages_completed",
                                "neo4j_llm_calls_active",
                                "neo4j_stage",
                                "neo4j_stage_completed",
                                "neo4j_stage_total",
                                "neo4j_node_write_batches_completed",
                                "neo4j_node_write_batches_total",
                                "neo4j_relationship_write_batches_completed",
                                "neo4j_relationship_write_batches_total",
                            ):
                                if field in details:
                                    current[field] = details[field]
                        current["status"] = "running"
                        await runs.service.write_manifest(current, storage = storage)

                async def cancel_check() -> bool:
                    await ensure_lease()
                    return await runs.service.cancel_requested(book_id, agentic_run_id)

                graph_extractor = (
                    ai.bd.agents.ingestion.graph_extractor.agent.graph_extractor(
                        _build_graph_extractor_model(),
                    )
                    if manifest.get("node_status", {}).get("neo4j") != "done"
                    else None
                )
                context = ai.bd.graphs.ingestion.rag_indexing.entities.ProcessedBookIndexDependencies(
                    storage = storage,
                    graph_extractor = graph_extractor,
                    progress_callback = report_progress,
                    cancel_check = cancel_check,
                    embedding_model = embedding_profile[0] if embedding_profile else None,
                    embedding_dimensions = embedding_profile[1] if embedding_profile else None,
                )
                graph_input = None if snapshot.next else initial_state
                await graph.ainvoke(
                    graph_input,
                    config = config,
                    context = context,
                    durability = "sync",
                )
                await ensure_lease()
                if await runs.service.cancel_requested(book_id, agentic_run_id):
                    raise RuntimeError("processed-book retrieval indexing was cancelled")
                completed = await runs.service.read_index(book_id, agentic_run_id, storage = storage)
                if completed is None:
                    raise FileNotFoundError("retrieval-index manifest disappeared after execution")
                completed.update(
                    status = "done",
                    cancel_requested = False,
                    current_node = None,
                    replay_from_node = None,
                    finished_at = max(time.time(), completed["created_at"]),
                    error = None,
                )
                completed = await runs.service.write_manifest(completed, storage = storage)
                if completed.get("qdrant_collection"):
                    try:
                        removed_points = await source.indexes.qdrant_index.service.prune_stale_book_points(
                            collection = completed["qdrant_collection"],
                            book_id = book_id,
                            current_agentic_run_id = agentic_run_id,
                            current_revision = completed["processed_book_revision"],
                            previous_collection = completed.get("previous_qdrant_collection"),
                            previous_agentic_run_id = completed.get("previous_agentic_run_id"),
                            previous_revision = completed.get("previous_processed_book_revision"),
                            current_profile_fingerprint = completed.get("qdrant_index_profile_fingerprint"),
                            previous_profile_fingerprint = completed.get(
                                "previous_qdrant_index_profile_fingerprint",
                            ),
                        )
                        logger.info(
                            "[bd-retrieval-index] pruned %s stale Qdrant points for book %s",
                            removed_points,
                            book_id,
                        )
                    except Exception:
                        logger.exception(
                            "[bd-retrieval-index] stale-point cleanup failed after successful publish for %s",
                            book_id,
                        )
                await settings.llm_counter.service.snapshot(
                    "bd",
                    keys.usage_run_id(book_id, agentic_run_id),
                )
                return {
                    "book_id": book_id,
                    "agentic_run_id": agentic_run_id,
                    "status": completed["status"],
                    "processed_page_count": completed["processed_page_count"],
                }
            except Exception as error:
                if lease_lost.is_set():
                    logger.warning(
                        "[bd-retrieval-index] worker lost the lease for %s; checkpoint is resumable",
                        agentic_run_id,
                    )
                    return {"book_id": book_id, "agentic_run_id": agentic_run_id, "status": "lease_lost"}
                cancelled = await runs.service.cancel_requested(book_id, agentic_run_id)
                if cancelled:
                    logger.info("[bd-retrieval-index] cancellation reached a safe boundary for %s", agentic_run_id)
                else:
                    logger.exception("[bd-retrieval-index] indexing failed for %s", agentic_run_id)
                failed = await runs.service.read_index(book_id, agentic_run_id, storage = storage)
                if failed is not None:
                    status = "cancelled" if cancelled else "failed"
                    current_node = failed.get("current_node")
                    if isinstance(error, errors.EmbeddingPreflightError):
                        failed["node_status"]["qdrant"] = status
                    if current_node in failed["node_status"]:
                        failed["node_status"][current_node] = status
                    failed.update(
                        status = status,
                        cancel_requested = False,
                        current_node = None,
                        finished_at = max(time.time(), failed["created_at"]),
                        error = None if cancelled else (
                            str(error)
                            if isinstance(error, errors.EmbeddingPreflightError)
                            else f"Retrieval indexing failed ({type(error).__name__})."
                        ),
                    )
                    await runs.service.write_manifest(failed, storage = storage)
                    await settings.llm_counter.service.snapshot(
                        "bd",
                        keys.usage_run_id(book_id, agentic_run_id),
                    )
                if cancelled:
                    return {"book_id": book_id, "agentic_run_id": agentic_run_id, "status": "cancelled"}
                raise
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
                if checkpointer_initialized:
                    await infra.postgres.service.close_checkpointer()
                await settings.chat.service.close_shared_http_client()
                await infra.redis.service.release_if_owner(redis, lock_key, owner)

    result = asyncio.run(execute())
    if result.get("execution_lock_busy"):
        raise self.retry(countdown = params.RUN_LOCK_RETRY_S, max_retries = None)
    return result


def _build_graph_extractor_model():
    settings.chat.service.refresh_endpoint(force = True)
    return settings.chat.service.build_chat_model(
        timeout_s = params.GRAPH_EXTRACTION_TIMEOUT_S,
        rotator_task = params.GRAPH_ROTATOR_TASK,
    )
