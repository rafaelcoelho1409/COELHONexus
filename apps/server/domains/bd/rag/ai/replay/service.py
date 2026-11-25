"""Checkpoint replay and external-output cleanup for retrieval-index runs."""
from __future__ import annotations
import ai, infra
from . import domain
from .. import keys, params, runs, versions
from ... import source
from .... import ingestion
from ..... import settings

import time
from typing import Any


async def _checkpoint_for_replay(
    graph: Any,
    config: dict[str, Any],
    node_id: str,
) -> tuple[dict[str, Any], str | None]:
    """Find a safe checkpoint before a node, including one branch of the store fan-out."""
    if node_id not in {"qdrant", "neo4j"}:
        return await infra.postgres.service.checkpoint_before_node(graph, config, node_id), None

    async for snapshot in graph.aget_state_history(config):
        next_nodes = set(snapshot.next or ())
        if next_nodes == {"qdrant", "neo4j"}:
            return snapshot.config, "elasticsearch"
        if next_nodes == {node_id}:
            return snapshot.config, None
    raise ValueError(f"no saved checkpoint schedules retrieval-index node {node_id!r}")

async def prepare_graph_replay(graph: Any, config: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Fork the saved graph state immediately before a selected node."""
    checkpoint_config, as_node = await _checkpoint_for_replay(graph, config, node_id)
    snapshot = await graph.aget_state(config)
    state = snapshot.values if isinstance(snapshot.values, dict) else {}
    updated_state = domain.replay_state(state, node_id)
    if as_node is not None:
        return await graph.aupdate_state(checkpoint_config, updated_state, as_node = as_node)
    return await graph.aupdate_state(checkpoint_config, updated_state)

async def _delete_replay_outputs(
    manifest: dict[str, Any],
    node_id: str,
    storage: infra.minio.service.MinIOStorage,
) -> None:
    """Remove external outputs owned by the selected node and its invalidated descendants."""
    descendants = params.REPLAY_DESCENDANTS[node_id]
    book_id = manifest["book_id"]
    agentic_run_id = manifest["agentic_run_id"]
    revision = manifest["processed_book_revision"]

    if "pin_sources" in descendants:
        source_key = manifest.get("source_artifact_key")
        if isinstance(source_key, str) and source_key:
            await storage.delete(source_key)

    if "elasticsearch" in descendants:
        es_index = manifest.get("elasticsearch_index") or infra.elasticsearch.keys.INDEX_BD_INGESTION
        es = infra.elasticsearch.service.new_es()
        try:
            if es_index == infra.elasticsearch.keys.INDEX_BD_INGESTION:
                await source.indexes.elasticsearch_index.service.delete_book_revision(
                    es,
                    book_id = book_id,
                    agentic_run_id = agentic_run_id,
                    revision = revision,
                )
            else:
                await es.indices.delete(index = es_index, ignore_unavailable = True)
                await source.indexes.elasticsearch_index.service.delete_book_revision(
                    es,
                    book_id = book_id,
                    agentic_run_id = agentic_run_id,
                    revision = revision,
                    indexes = (infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES,),
                )
        finally:
            await es.close()

    if "qdrant" in descendants:
        collection = manifest.get("qdrant_collection")
        if collection:
            qdrant = infra.qdrant.service.new_qdrant()
            try:
                if await qdrant.collection_exists(collection):
                    models = infra.qdrant.service.models
                    await qdrant.delete(
                        collection_name = collection,
                        points_selector = models.FilterSelector(
                            filter = models.Filter(must = [
                                models.FieldCondition(key = "book_id", match = models.MatchValue(value = book_id)),
                                models.FieldCondition(
                                    key = "agentic_run_id",
                                    match = models.MatchValue(value = agentic_run_id),
                                ),
                                models.FieldCondition(
                                    key = "processed_book_revision",
                                    match = models.MatchValue(value = revision),
                                ),
                            ]),
                        ),
                    )
            finally:
                await qdrant.close()

    if "neo4j" in descendants:
        await source.indexes.neo4j_index.service.delete_graph(
            book_id,
            agentic_run_id,
            revision,
        )

async def replay_index_node(book_id: str, agentic_run_id: str, node_id: str) -> dict[str, Any]:
    """Queue a terminal retrieval-index run from one node, clearing only downstream results."""
    if not runs.domain.safe_segment(book_id) or not runs.domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if node_id not in params.REPLAY_DESCENDANTS:
        raise ValueError(f"unknown retrieval-index graph node {node_id!r}")
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis, redis.lock(
        ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
        timeout = 120,
        blocking_timeout = 30,
    ), redis.lock(
        keys.enqueue_lock_key(book_id, agentic_run_id),
        timeout = 120,
        blocking_timeout = 30,
    ):
        manifest = await runs.service.read_index(book_id, agentic_run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError("retrieval-index run not found")
        replay_compatible_legacy_graph = (
            node_id == "neo4j"
            and manifest["workflow_version"]
            in versions.NEO4J_REPLAY_COMPATIBLE_WORKFLOW_VERSIONS
        )
        if (
            manifest["workflow_version"] != versions.WORKFLOW_VERSION
            and not replay_compatible_legacy_graph
        ):
            raise ValueError("this retrieval-index run uses an older graph; start a fresh Agentic RAG run")
        if manifest["status"] in {"queued", "running"}:
            age = time.time() - manifest["updated_at"]
            if age < params.STALE_JOB_AFTER_S or await redis.exists(keys.lock_key(book_id, agentic_run_id)):
                raise ValueError("stop the active Agentic RAG run before replaying a node")
            manifest["status"] = "failed"
        elif manifest["status"] not in {"done", "failed", "cancelled"}:
            raise ValueError("only a stopped Agentic RAG run can replay a node")
        if await redis.exists(keys.lock_key(book_id, agentic_run_id)):
            raise ValueError("stop the active Agentic RAG run before replaying a node")

        run = await ingestion.ai.service.read_ingestion_ai_run(
            book_id,
            agentic_run_id,
            storage = storage,
        )
        if run is None or run.get("status") != "done":
            raise ValueError("Agentic RAG replay requires a completed Ingestion run")
        current = await ingestion.ai.processed_book.service.read_current(
            book_id,
            agentic_run_id,
            storage = storage,
        )
        if current is None:
            raise ValueError("the published processed book is no longer available")
        processed, _, processed_sha256 = current
        if (
            processed.extraction_run_id != manifest["extraction_run_id"]
            or processed.selection_revision != manifest["processed_book_revision"]
            or processed_sha256 != manifest["processed_book_sha256"]
        ):
            raise ValueError("the retrieval index no longer matches the current processed-book revision")

        incomplete = [
            predecessor
            for predecessor in params.REPLAY_PREREQUISITES[node_id]
            if manifest["node_status"].get(predecessor) != "done"
        ]
        if incomplete:
            raise ValueError(
                f"Cannot replay {node_id} yet: {incomplete[0]} has not completed. "
                f"Resume the Agentic RAG run or replay {incomplete[0]}."
            )


        graph = ai.bd.graphs.rag.index.graphs.retrieval_index_graph(
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
        await _checkpoint_for_replay(graph, config, node_id)
        await _delete_replay_outputs(manifest, node_id, storage)


        descendants = params.REPLAY_DESCENDANTS[node_id]
        for node in descendants:
            manifest["node_status"][node] = "pending"
        for downstream_node in descendants:
            for field, value in params.NODE_OUTPUT_DEFAULTS[downstream_node].items():
                manifest[field] = None if value == "" or field == "embedding_dimensions" else value
        if "neo4j" in descendants:
            manifest["graph_batches_completed"] = 0
            manifest["graph_batches_total"] = 0
        for node, field in (
            ("pin_sources", "pin_sources_pages_completed"),
            ("elasticsearch", "elasticsearch_pages_completed"),
            ("qdrant", "qdrant_pages_completed"),
            ("neo4j", "neo4j_pages_completed"),
        ):
            if node in descendants:
                manifest[field] = 0
        await settings.llm_counter.service.retain_nodes(
            "bd",
            keys.usage_run_id(book_id, agentic_run_id),
            domain.retained_llm_nodes(node_id),
        )

        manifest.update(
            status = "queued",
            cancel_requested = False,
            current_node = None,
            replay_from_node = node_id,
            attempt = manifest["attempt"] + 1,
            finished_at = None,
            error = None,
        )
        manifest = await runs.service.write_manifest(manifest, storage = storage)
        await redis.delete(keys.cancel_key(book_id, agentic_run_id))
        try:
            infra.celery.service.app.send_task(params.TASK_NAME, args = (book_id, agentic_run_id))
        except Exception as error:  # noqa: BLE001 — persist a terminal run if broker dispatch fails.
            manifest.update(
                status = "failed",
                replay_from_node = None,
                error = f"Unable to queue retrieval-index replay ({type(error).__name__}).",
                finished_at = max(time.time(), manifest["created_at"]),
            )
            return await runs.service.write_manifest(manifest, storage = storage)
        return manifest
