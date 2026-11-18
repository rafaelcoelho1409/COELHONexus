"""Application services for retrieval-index run lifecycle."""
from __future__ import annotations
import infra
from . import domain, schemas
from .. import keys, params, versions
from ... import source
from .... import ingestion
from ..... import settings

import json
import time
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)

async def write_manifest(
    manifest: dict[str, Any],
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any]:
    if storage is None:
        storage = infra.minio.service.get_storage()
    manifest["schema_version"] = versions.SCHEMA_VERSION
    manifest["updated_at"] = max(time.time(), manifest["created_at"])
    parsed = schemas.RetrievalIndexManifest.model_validate(manifest)
    await storage.write(
        keys.manifest_key(
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
    if not domain.safe_segment(book_id) or not domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    if storage is None:
        storage = infra.minio.service.get_storage()
    key = keys.manifest_key(book_id, agentic_run_id)
    if not await storage.exists(key):
        return None
    document = json.loads(await storage.read_text(key))
    if not isinstance(document, dict):
        raise TypeError("the retrieval-index manifest must be a JSON object")
    if "qdrant_index_profile_fingerprint" not in document:
        document["qdrant_index_profile_fingerprint"] = ""
    manifest = schemas.RetrievalIndexManifest.model_validate(document)
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

async def read_current_index(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    if not domain.safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    storage = storage or infra.minio.service.get_storage()
    pointer_key = ingestion.source.storage.keys.current_retrieval_index_key(book_id)
    if not await storage.exists(pointer_key):
        return None
    pointer = json.loads(await storage.read_text(pointer_key))
    run_id = pointer.get("agentic_run_id")
    if not isinstance(run_id, str) or not domain.safe_segment(run_id):
        raise ValueError("the current retrieval-index pointer is malformed")
    manifest = await read_index(book_id, run_id, storage = storage)
    if manifest is None or manifest.get("status") != "done":
        return None
    if manifest.get("processed_book_revision") != pointer.get("processed_book_revision"):
        return None
    current = await ingestion.ai.processed_book.service.read_current(
        book_id,
        run_id,
        storage = storage,
    )
    if current is None or current[2] != pointer.get("processed_book_sha256"):
        return None
    return {**pointer, "status": manifest["status"], "index": manifest}

async def queue_index(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    """Idempotently queue the post-publication retrieval branch for one completed run."""
    if not domain.safe_segment(book_id) or not domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    run = await ingestion.ai.service.read_ingestion_ai_run(
        book_id,
        agentic_run_id,
        storage = storage,
    )
    if run is None:
        raise FileNotFoundError(f"Agentic ingestion run {agentic_run_id} was not found")
    if run.get("status") != "done":
        raise ValueError("retrieval indexing requires a completed Ingestion run")
    current = await ingestion.ai.processed_book.service.read_current(
        book_id,
        agentic_run_id,
        storage = storage,
    )
    if current is None:
        raise ValueError("retrieval indexing requires a published processed book")
    processed, processed_key, processed_sha256 = current
    if processed.extraction_run_id != run.get("extraction_run_id"):
        raise ValueError("the processed book does not match the completed Ingestion run")

    async with infra.redis.service.client() as redis, redis.lock(
        keys.enqueue_lock_key(book_id, agentic_run_id),
        timeout = 120,
        blocking_timeout = 30,
    ):
        existing = await read_index(book_id, agentic_run_id, storage = storage)
        now = time.time()
        if existing is not None and existing.get("status") in {"queued", "running"}:
            return existing
        same_inputs = existing is not None and (
            existing.get("workflow_version") == versions.WORKFLOW_VERSION
            and existing.get("processed_book_revision") == processed.selection_revision
            and existing.get("processed_book_sha256") == processed_sha256
        )
        same_revision = same_inputs and (
            existing.get("qdrant_index_profile_fingerprint")
            == source.indexes.qdrant_index.domain.index_profile_fingerprint()
        )
        if same_revision:
            configured_model = settings.embeddings.service.get_configured_model()
            if configured_model.casefold() == "auto" or existing.get("embedding_model") == configured_model:
                return existing
        manifest = domain.initial_manifest(
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
        if same_inputs and existing is not None:
            manifest["checkpoint_generation"] = existing["attempt"] + 1
        manifest["qdrant_index_profile_fingerprint"] = (
            source.indexes.qdrant_index.domain.index_profile_fingerprint()
        )
        pointer_key = ingestion.source.storage.keys.current_retrieval_index_key(book_id)
        if await storage.exists(pointer_key):
            try:
                previous_pointer = json.loads(await storage.read_text(pointer_key))
            except (TypeError, ValueError):
                previous_pointer = {}
            if not isinstance(previous_pointer, dict):
                previous_pointer = {}
            previous_run_id = previous_pointer.get("agentic_run_id")
            previous_revision = previous_pointer.get("processed_book_revision")
            if (
                domain.safe_segment(previous_run_id)
                and isinstance(previous_revision, int)
                and not isinstance(previous_revision, bool)
                and previous_revision > 0
            ):
                previous_collection = previous_pointer.get("qdrant_collection")
                manifest.update(
                    previous_qdrant_collection = previous_collection if isinstance(previous_collection, str) else None,
                    previous_agentic_run_id = previous_run_id,
                    previous_processed_book_revision = previous_revision,
                    previous_qdrant_index_profile_fingerprint = previous_pointer.get(
                        "qdrant_index_profile_fingerprint",
                    ),
                )
        manifest = await write_manifest(manifest, storage = storage)
        try:
            infra.celery.service.app.send_task(params.TASK_NAME, args = (book_id, agentic_run_id))
        except Exception as error:  # noqa: BLE001 — persist a terminal run if broker dispatch fails.
            manifest.update(
                status = "failed",
                error = f"Unable to queue retrieval indexing ({type(error).__name__}).",
                finished_at = max(time.time(), manifest["created_at"]),
            )
            return await write_manifest(manifest, storage = storage)
        return manifest

async def resume_index(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis, redis.lock(
        keys.enqueue_lock_key(book_id, agentic_run_id),
        timeout = 120,
        blocking_timeout = 30,
    ):
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
        try:
            infra.celery.service.app.send_task(params.TASK_NAME, args = (book_id, agentic_run_id))
        except Exception as error:  # noqa: BLE001 — persist a terminal run if broker dispatch fails.
            manifest.update(
                status = "failed",
                error = f"Unable to queue retrieval-index resume ({type(error).__name__}).",
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
    if not domain.safe_segment(book_id) or not domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis, redis.lock(
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
                await source.indexes.elasticsearch_index.service.delete_book_revision(
                    es,
                    book_id = book_id,
                    agentic_run_id = manifest["agentic_run_id"],
                    revision = revision,
                )
            else:
                # Previously published manifests may still point to an isolated per-book index.
                await es.indices.delete(index = es_index, ignore_unavailable = True)
                await source.indexes.elasticsearch_index.service.delete_book_revision(
                    es,
                    book_id = book_id,
                    agentic_run_id = manifest["agentic_run_id"],
                    revision = revision,
                    indexes = (infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES,),
                )
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

        await source.indexes.neo4j_index.service.delete_graph(
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
                manifest.get("qdrant_index_profile_fingerprint"),
                manifest.get("checkpoint_generation", 0),
            )
        )

        pointer_key = ingestion.source.storage.keys.current_retrieval_index_key(book_id)
        if await storage.exists(pointer_key):
            pointer = json.loads(await storage.read_text(pointer_key))
            if (
                pointer.get("agentic_run_id") == agentic_run_id
                and pointer.get("processed_book_revision") == revision
            ):
                await storage.delete(pointer_key)

        objects_deleted = await storage.delete_prefix(
            ingestion.source.storage.keys.agentic_retrieval_index_prefix(
                book_id,
                agentic_run_id,
            ),
        )
        await redis.delete(keys.cancel_key(book_id, agentic_run_id))
        await settings.llm_counter.service.delete_run(
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
