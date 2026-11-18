"""Build and operate a retrieval index from the canonical processed-book revision."""
from __future__ import annotations

import json
import time
from typing import Any

import domains
import infra

from . import keys, params, schemas, versions


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def _safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


def _initial_status() -> dict[str, str]:
    return schemas.RetrievalIndexManifest.initial_node_status()


async def write_manifest(
    manifest: dict[str, Any],
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    if storage is None:
        storage = infra.minio.service.get_storage()
    manifest["updated_at"] = max(time.time(), manifest["created_at"])
    parsed = schemas.RetrievalIndexManifest.model_validate(manifest)
    await storage.write(
        domains.bd.ingestion.ai.retrieval_index.keys.manifest_key(
            parsed.book_id,
            parsed.agentic_run_id,
        ),
        _json(parsed.model_dump(mode = "json")),
        content_type = "application/json",
    )
    return parsed.model_dump(mode = "json")


async def read_index(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    key = domains.bd.ingestion.ai.retrieval_index.keys.manifest_key(book_id, agentic_run_id)
    if not await storage.exists(key):
        return None
    manifest = schemas.RetrievalIndexManifest.model_validate_json(await storage.read_text(key))
    if manifest.book_id != book_id or manifest.agentic_run_id != agentic_run_id:
        raise ValueError("the retrieval-index manifest does not match the requested Ingestion run")
    result = manifest.model_dump(mode = "json")
    if result["status"] not in {"queued", "running"}:
        return result

    async with infra.redis.service.client() as redis:
        cancel_key = keys.cancel_key(book_id, agentic_run_id)
        cancel_requested = result["cancel_requested"] or await infra.redis.service.flag_is_set(
            redis,
            cancel_key,
        )
        result["cancel_requested"] = cancel_requested
        if not cancel_requested or await redis.exists(keys.lock_key(book_id, agentic_run_id)):
            return result

        # A worker can be killed during a deploy after Stop is requested. Once its
        # execution lease is gone, no worker remains to publish the cancelled state.
        current_node = result.get("current_node")
        if current_node in result["node_status"] and result["node_status"][current_node] == "running":
            result["node_status"][current_node] = "cancelled"
        result.update(
            status = "cancelled",
            cancel_requested = False,
            current_node = None,
            finished_at = max(time.time(), result["created_at"]),
            error = None,
        )
        result = await write_manifest(result, storage = storage)
        await redis.delete(cancel_key)
    return result


async def read_sources(
    state: dict[str, Any],
    storage: infra.minio.service.MinIOStorage,
) -> list[dict[str, Any]]:
    key = state.get("source_artifact_key")
    if not isinstance(key, str):
        raise ValueError("the pinned source artifact is missing")
    content = await storage.read_text(key)
    rows = []
    for line in content.splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("content"), str):
                raise ValueError("the pinned source artifact contains a malformed page record")
            rows.append(row)
    if not rows:
        raise ValueError("the pinned source artifact contains no pages")
    return rows


async def read_current_index(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    if not _safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    storage = storage or infra.minio.service.get_storage()
    pointer_key = domains.bd.ingestion.storage.keys.current_retrieval_index_key(book_id)
    if not await storage.exists(pointer_key):
        return None
    pointer = json.loads(await storage.read_text(pointer_key))
    run_id = pointer.get("agentic_run_id")
    if not isinstance(run_id, str) or not _safe_segment(run_id):
        raise ValueError("the current retrieval-index pointer is malformed")
    manifest = await read_index(book_id, run_id, storage = storage)
    if manifest is None or manifest.get("status") != "done":
        return None
    if manifest.get("processed_book_revision") != pointer.get("processed_book_revision"):
        return None
    current = await domains.bd.ingestion.ai.processed_book.service.read_current(
        book_id,
        run_id,
        storage = storage,
    )
    if current is None or current[2] != pointer.get("processed_book_sha256"):
        return None
    return {**pointer, "status": manifest["status"], "index": manifest}


async def queue_index(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    """Idempotently queue the post-publication retrieval branch for one completed run."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    run = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(
        book_id,
        agentic_run_id,
        storage = storage,
    )
    if run is None:
        raise FileNotFoundError(f"Agentic ingestion run {agentic_run_id} was not found")
    if run.get("status") != "done":
        raise ValueError("retrieval indexing requires a completed Ingestion run")
    current = await domains.bd.ingestion.ai.processed_book.service.read_current(
        book_id,
        agentic_run_id,
        storage = storage,
    )
    if current is None:
        raise ValueError("retrieval indexing requires a published processed book")
    processed, processed_key, processed_sha256 = current
    if processed.extraction_run_id != run.get("extraction_run_id"):
        raise ValueError("the processed book does not match the completed Ingestion run")

    async with infra.redis.service.client() as redis:
        async with redis.lock(
            keys.enqueue_lock_key(book_id, agentic_run_id),
            timeout = 120,
            blocking_timeout = 30,
        ):
            existing = await read_index(book_id, agentic_run_id, storage = storage)
            now = time.time()
            if existing is not None and existing.get("status") in {"queued", "running"}:
                return existing
            same_revision = existing is not None and (
                existing.get("workflow_version") == versions.WORKFLOW_VERSION
                and existing.get("processed_book_revision") == processed.selection_revision
                and existing.get("processed_book_sha256") == processed_sha256
            )
            if same_revision:
                return existing
            manifest = initial_manifest(
                book_id,
                run["extraction_run_id"],
                agentic_run_id,
                processed_key,
                processed_sha256,
                processed.selection_revision,
                len(processed.pages),
                attempt = existing["attempt"] + 1 if existing is not None else 1,
                created_at = existing["created_at"] if existing is not None else now,
            )
            manifest = await write_manifest(manifest, storage = storage)
            from . import task
            try:
                task.index_processed_book.apply_async(args = (book_id, agentic_run_id))
            except Exception as error:
                manifest.update(
                    status = "failed",
                    error = f"Unable to queue retrieval indexing ({type(error).__name__}).",
                    finished_at = max(time.time(), manifest["created_at"]),
                )
                return await write_manifest(manifest, storage = storage)
            return manifest


async def resume_index(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis:
        async with redis.lock(keys.enqueue_lock_key(book_id, agentic_run_id), timeout = 120, blocking_timeout = 30):
            manifest = await read_index(book_id, agentic_run_id, storage = storage)
            if manifest is None:
                raise FileNotFoundError("retrieval-index run not found")
            if manifest["workflow_version"] != versions.WORKFLOW_VERSION:
                raise ValueError("this retrieval-index run uses an older graph; start a fresh Agentic RAG run")
            if manifest["status"] in {"queued", "running"}:
                age = time.time() - manifest["updated_at"]
                if age < params.STALE_JOB_AFTER_S or await redis.exists(keys.lock_key(book_id, agentic_run_id)):
                    raise ValueError("the retrieval-index worker is still active")
            elif manifest["status"] not in {"failed", "cancelled"}:
                raise ValueError("only failed, cancelled, or stale retrieval indexes can be resumed")
            manifest["status"] = "queued"
            manifest["cancel_requested"] = False
            manifest["attempt"] += 1
            manifest["finished_at"] = None
            manifest["error"] = None
            manifest["current_node"] = None
            for node, status in manifest["node_status"].items():
                if status in {"failed", "cancelled", "running"}:
                    manifest["node_status"][node] = "pending"
            manifest = await write_manifest(manifest, storage = storage)
            await redis.delete(keys.cancel_key(book_id, agentic_run_id))
            from . import task
            try:
                task.index_processed_book.apply_async(args = (book_id, agentic_run_id))
            except Exception as error:
                manifest.update(
                    status = "failed",
                    error = f"Unable to queue retrieval-index resume ({type(error).__name__}).",
                    finished_at = max(time.time(), manifest["created_at"]),
                )
                return await write_manifest(manifest, storage = storage)
            return manifest


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
    from . import domain

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
        index_domain = domains.bd.ingestion.ai.retrieval_index.elasticsearch_index.domain
        es = infra.elasticsearch.service.new_es()
        try:
            if es_index == infra.elasticsearch.keys.INDEX_BD_INGESTION and await es.indices.exists(index = es_index):
                result = await es.delete_by_query(
                    index = es_index,
                    query = {
                        "bool": {
                            "filter": [
                                {"term": {"book_id": book_id}},
                                {"term": {"book_fingerprint": index_domain.book_fingerprint(book_id)}},
                                {"term": {"agentic_run_id": agentic_run_id}},
                                {"term": {"processed_book_revision": revision}},
                            ],
                        },
                    },
                    conflicts = "proceed",
                    refresh = True,
                )
                if result.get("timed_out") or result.get("failures") or result.get("version_conflicts", 0):
                    raise RuntimeError("Elasticsearch could not fully clear the replayed book revision")
            elif es_index != infra.elasticsearch.keys.INDEX_BD_INGESTION:
                await es.indices.delete(index = es_index, ignore_unavailable = True)
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
        await domains.bd.ingestion.ai.retrieval_index.neo4j_index.service.delete_graph(
            book_id,
            agentic_run_id,
            revision,
        )


async def replay_index_node(book_id: str, agentic_run_id: str, node_id: str) -> dict[str, Any]:
    """Queue a terminal retrieval-index run from one node, clearing only downstream results."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if node_id not in params.REPLAY_DESCENDANTS:
        raise ValueError(f"unknown retrieval-index graph node {node_id!r}")
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis:
        async with redis.lock(keys.enqueue_lock_key(book_id, agentic_run_id), timeout = 120, blocking_timeout = 30):
            manifest = await read_index(book_id, agentic_run_id, storage = storage)
            if manifest is None:
                raise FileNotFoundError("retrieval-index run not found")
            if manifest["workflow_version"] != versions.WORKFLOW_VERSION:
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

            run = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(
                book_id,
                agentic_run_id,
                storage = storage,
            )
            if run is None or run.get("status") != "done":
                raise ValueError("Agentic RAG replay requires a completed Ingestion run")
            current = await domains.bd.ingestion.ai.processed_book.service.read_current(
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

            import ai

            graph = ai.bd.graphs.rag.graphs.retrieval_index_graph(
                checkpointer = infra.postgres.service.get_checkpointer(),
            )
            config = {"configurable": {"thread_id": keys.thread_id(
                book_id,
                manifest["extraction_run_id"],
                agentic_run_id,
                manifest["workflow_version"],
                manifest["processed_book_revision"],
            )}}
            await _checkpoint_for_replay(graph, config, node_id)
            await _delete_replay_outputs(manifest, node_id, storage)

            from . import domain

            descendants = domain.params.REPLAY_DESCENDANTS[node_id]
            for node in descendants:
                manifest["node_status"][node] = "pending"
            for downstream_node in descendants:
                for field, value in domain.NODE_OUTPUT_DEFAULTS[downstream_node].items():
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
            await domains.settings.llm_counter.service.retain_nodes(
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
            manifest = await write_manifest(manifest, storage = storage)
            await redis.delete(keys.cancel_key(book_id, agentic_run_id))
            from . import task
            try:
                task.index_processed_book.apply_async(args = (book_id, agentic_run_id))
            except Exception as error:
                manifest.update(
                    status = "failed",
                    replay_from_node = None,
                    error = f"Unable to queue retrieval-index replay ({type(error).__name__}).",
                    finished_at = max(time.time(), manifest["created_at"]),
                )
                return await write_manifest(manifest, storage = storage)
            return manifest


async def request_cancel(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis:
        manifest = await read_index(book_id, agentic_run_id, storage = storage)
        if manifest is None:
            raise FileNotFoundError("retrieval-index run not found")
        if manifest["status"] not in {"queued", "running"}:
            return manifest
        await redis.set(keys.cancel_key(book_id, agentic_run_id), "1", ex = params.CANCEL_FLAG_TTL_S)
        # Let the worker own terminal status updates. If it has already disappeared,
        # read_index reconciles the stop request after checking the execution lease.
        return await read_index(book_id, agentic_run_id, storage = storage) or manifest


async def wipe_index(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    """Delete one stopped retrieval index without touching its processed-book source."""
    if not _safe_segment(book_id) or not _safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis:
        async with redis.lock(
            keys.enqueue_lock_key(book_id, agentic_run_id),
            timeout = 120,
            blocking_timeout = 30,
        ):
            manifest = await read_index(book_id, agentic_run_id, storage = storage)
            if manifest is None:
                raise FileNotFoundError("retrieval-index run not found")
            if manifest["status"] in {"queued", "running"}:
                raise ValueError("stop the Agentic RAG run and wait for it to finish before wiping it")
            if await redis.exists(keys.lock_key(book_id, agentic_run_id)):
                raise ValueError("the retrieval-index worker is still active")

            # A delayed Celery delivery must observe a terminal status before the manifest is removed.
            manifest.update(
                status = "cancelled",
                current_node = None,
                finished_at = max(time.time(), manifest["created_at"]),
                error = None,
            )
            await write_manifest(manifest, storage = storage)
            if await redis.exists(keys.lock_key(book_id, agentic_run_id)):
                raise ValueError("the retrieval-index worker is still active")

            revision = manifest["processed_book_revision"]
            es_index = manifest.get("elasticsearch_index") or infra.elasticsearch.keys.INDEX_BD_INGESTION
            es = infra.elasticsearch.service.new_es()
            try:
                if es_index == infra.elasticsearch.keys.INDEX_BD_INGESTION:
                    if await es.indices.exists(index = es_index):
                        index_domain = domains.bd.ingestion.ai.retrieval_index.elasticsearch_index.domain
                        result = await es.delete_by_query(
                            index = es_index,
                            query = {
                                "bool": {
                                    "filter": [
                                        {"term": {"book_id": book_id}},
                                        {"term": {"book_fingerprint": index_domain.book_fingerprint(book_id)}},
                                        {"term": {"agentic_run_id": agentic_run_id}},
                                        {"term": {"processed_book_revision": revision}},
                                    ],
                                },
                            },
                            conflicts = "proceed",
                            refresh = True,
                        )
                        if (
                            result.get("timed_out")
                            or result.get("failures")
                            or result.get("version_conflicts", 0)
                        ):
                            raise RuntimeError("Elasticsearch could not fully wipe this book revision")
                else:
                    # Previously published manifests may still point to an isolated per-book index.
                    await es.indices.delete(index = es_index, ignore_unavailable = True)
            finally:
                await es.close()

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
                                    models.FieldCondition(
                                        key = "book_id",
                                        match = models.MatchValue(value = book_id),
                                    ),
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

            await domains.bd.ingestion.ai.retrieval_index.neo4j_index.service.delete_graph(
                book_id,
                agentic_run_id,
                revision,
            )
            checkpointer = await infra.postgres.service.init_checkpointer()
            await checkpointer.adelete_thread(
                keys.thread_id(
                    book_id,
                    manifest["extraction_run_id"],
                    agentic_run_id,
                    manifest["workflow_version"],
                    revision,
                )
            )

            pointer_key = domains.bd.ingestion.storage.keys.current_retrieval_index_key(book_id)
            if await storage.exists(pointer_key):
                pointer = json.loads(await storage.read_text(pointer_key))
                if (
                    pointer.get("agentic_run_id") == agentic_run_id
                    and pointer.get("processed_book_revision") == revision
                ):
                    await storage.delete(pointer_key)

            objects_deleted = await storage.delete_prefix(
                domains.bd.ingestion.storage.keys.agentic_retrieval_index_prefix(
                    book_id,
                    agentic_run_id,
                ),
            )
            await redis.delete(keys.cancel_key(book_id, agentic_run_id))
            await domains.settings.llm_counter.service.delete_run(
                "bd",
                keys.usage_run_id(book_id, agentic_run_id),
            )
            return {
                "book_id": book_id,
                "agentic_run_id": agentic_run_id,
                "wiped": True,
                "objects_deleted": objects_deleted,
            }


async def cancel_requested(book_id: str, agentic_run_id: str) -> bool:
    async with infra.redis.service.client() as redis:
        return await infra.redis.service.flag_is_set(redis, keys.cancel_key(book_id, agentic_run_id))


def initial_manifest(
    book_id: str,
    extraction_run_id: str,
    agentic_run_id: str,
    processed_book_key: str,
    processed_book_sha256: str,
    processed_book_revision: int,
    processed_page_count: int,
    *,
    attempt: int = 1,
    created_at: float | None = None,
) -> dict[str, Any]:
    now = created_at or time.time()
    return {
        "book_id": book_id,
        "extraction_run_id": extraction_run_id,
        "agentic_run_id": agentic_run_id,
        "processed_book_key": processed_book_key,
        "processed_book_sha256": processed_book_sha256,
        "processed_book_revision": processed_book_revision,
        "processed_page_count": processed_page_count,
        "status": "queued",
        "cancel_requested": False,
        "current_node": None,
        "node_status": _initial_status(),
        "attempt": attempt,
        "pin_sources_pages_completed": 0,
        "elasticsearch_pages_completed": 0,
        "qdrant_pages_completed": 0,
        "neo4j_pages_completed": 0,
        "graph_batches_completed": 0,
        "graph_batches_total": 0,
        "neo4j_stage": "idle",
        "neo4j_stage_completed": 0,
        "neo4j_stage_total": 0,
        "neo4j_node_write_batches_completed": 0,
        "neo4j_node_write_batches_total": 0,
        "neo4j_relationship_write_batches_completed": 0,
        "neo4j_relationship_write_batches_total": 0,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "error": None,
    }

