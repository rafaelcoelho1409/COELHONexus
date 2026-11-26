"""Application services for retrieval-index run lifecycle."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import infra

from ..... import settings
from .... import ingestion
from ... import source
from .. import keys, params, versions
from . import domain, schemas


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
    if not isinstance(pointer, dict):
        raise TypeError("the current retrieval-index pointer must be a JSON object")
    run_id = pointer.get("agentic_run_id")
    if not isinstance(run_id, str) or not domain.safe_segment(run_id):
        raise ValueError("the current retrieval-index pointer is malformed")
    manifest = await read_index(book_id, run_id, storage = storage)
    if manifest is None:
        return None
    pointer_manifest_key = pointer.get("manifest_key")
    if pointer_manifest_key is not None and pointer_manifest_key != keys.manifest_key(book_id, run_id):
        return None
    if manifest.get("book_id") != book_id or manifest.get("extraction_run_id") != pointer.get("extraction_run_id"):
        return None
    if manifest.get("processed_book_revision") != pointer.get("processed_book_revision"):
        return None
    pointer_attempt = pointer.get("attempt")
    manifest_attempt = manifest.get("attempt")
    if "attempt" in pointer and (
        type(pointer_attempt) is not int or pointer_attempt != manifest_attempt
    ):
        return None
    publication_committed = manifest.get("status") == "done"
    if (
        not publication_committed
        and type(pointer_attempt) is int
        and pointer_attempt == manifest_attempt
        and manifest.get("status") in {"running", "failed", "cancelled"}
    ):
        # The pointer is written only after all backend counts and source hashes validate.
        # Matching attempt IDs let readers serve it even if the worker exits before its
        # final manifest write, while a later retry/replay invalidates stale pointers.
        publication_committed = True
    if not publication_committed:
        return None
    current = await ingestion.ai.processed_book.service.read_current(
        book_id,
        run_id,
        storage = storage,
    )
    if current is None or current[2] != pointer.get("processed_book_sha256"):
        return None
    return {
        **pointer,
        "status": "done",
        "run_status": manifest["status"],
        "index": manifest,
    }


async def has_active_index_run(book_id: str) -> bool:
    """Check every retrieval-index run and execution lease for one book."""
    storage = infra.minio.service.get_storage()
    run_ids = await storage.list_subfolders(ingestion.source.storage.keys.agentic_runs_prefix(book_id))
    async with infra.redis.service.client() as redis:
        for agentic_run_id in run_ids:
            if agentic_run_id == "current" or not domain.safe_segment(agentic_run_id):
                continue
            if await redis.exists(keys.lock_key(book_id, agentic_run_id)):
                return True
            manifest = await read_index(book_id, agentic_run_id, storage = storage)
            if manifest is not None and manifest["status"] in {"queued", "running"}:
                return True
    return False


async def list_book_indexes(
    book_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> list[dict[str, Any]]:
    """Load every valid retrieval-index manifest owned by a book."""
    if not domain.safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    storage = storage or infra.minio.service.get_storage()
    run_ids = await storage.list_subfolders(ingestion.source.storage.keys.agentic_runs_prefix(book_id))
    manifests = await asyncio.gather(*(
        read_index(book_id, run_id, storage = storage)
        for run_id in run_ids
        if run_id != "current" and domain.safe_segment(run_id)
    ))
    return [manifest for manifest in manifests if manifest is not None]


async def queue_index(book_id: str, agentic_run_id: str) -> dict[str, Any]:
    """Idempotently queue the post-publication retrieval branch for one completed run."""
    if not domain.safe_segment(book_id) or not domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    async with infra.redis.service.client() as redis, redis.lock(
        ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
        timeout = 120,
        blocking_timeout = 30,
    ):
        return await _queue_index_locked(redis, book_id, agentic_run_id)


async def _queue_index_locked(redis, book_id: str, agentic_run_id: str) -> dict[str, Any]:
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

    async with redis.lock(
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
        ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
        timeout = 120,
        blocking_timeout = 30,
    ), redis.lock(keys.enqueue_lock_key(book_id, agentic_run_id), timeout = 120, blocking_timeout = 30):
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
    """Delete all BD retrieval projections for one book, preserving its processed source."""
    if not domain.safe_segment(book_id) or not domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis, redis.lock(
        ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
        timeout = 120,
        blocking_timeout = 30,
    ), redis.lock(keys.enqueue_lock_key(book_id, agentic_run_id), timeout = 120, blocking_timeout = 30):
        if await ingestion.source.runtime.progress.service.read_lock(redis, book_id):
            raise ValueError("stop Docling extraction before wiping this book's retrieval indexes")
        active_ingestion_run = await ingestion.ai.service.find_active_ingestion_ai_run(
            book_id,
            storage = storage,
        )
        if active_ingestion_run is not None:
            raise ValueError("stop all BD Agentic AI runs for this book before wiping its retrieval indexes")

        run_ids = set(await storage.list_subfolders(ingestion.source.storage.keys.agentic_runs_prefix(book_id)))
        run_ids.add(agentic_run_id)
        manifests: dict[str, dict[str, Any]] = {}
        for run_id in sorted(run_ids):
            if not domain.safe_segment(run_id):
                continue
            if await ingestion.ai.progress.service.run_execution_lock_is_held(redis, book_id, run_id):
                raise ValueError("wait for all BD Agentic AI workers to stop before wiping this book's indexes")
            if await redis.exists(keys.lock_key(book_id, run_id)):
                raise ValueError("stop all BD Agentic RAG runs for this book before wiping its indexes")
            run_manifest = await read_index(book_id, run_id, storage = storage)
            if run_id == agentic_run_id and run_manifest is None:
                raise FileNotFoundError("retrieval-index run not found")
            if run_manifest is None:
                continue
            if run_manifest["status"] in {"queued", "running"}:
                raise ValueError("stop all BD Agentic RAG runs for this book before wiping its indexes")
            manifests[run_id] = run_manifest

        if agentic_run_id not in manifests:
            raise FileNotFoundError("retrieval-index run not found")

        # Prevent delayed deliveries from rebuilding an index while the book-wide purge runs.
        for run_id, run_manifest in manifests.items():
            run_manifest.update(
                status = "cancelled",
                cancel_requested = False,
                current_node = None,
                finished_at = max(time.time(), run_manifest["created_at"]),
                error = None,
            )
            await write_manifest(run_manifest, storage = storage)

        legacy_indexes = {
            run_manifest["elasticsearch_index"]
            for run_manifest in manifests.values()
            if isinstance(run_manifest.get("elasticsearch_index"), str)
            and run_manifest["elasticsearch_index"]
            and run_manifest["elasticsearch_index"] != infra.elasticsearch.keys.INDEX_BD_INGESTION
        }
        if legacy_indexes:
            es = infra.elasticsearch.service.new_es()
            try:
                for index_name in sorted(legacy_indexes):
                    await es.indices.delete(index = index_name, ignore_unavailable = True)
            finally:
                await es.close()

        await source.indexes.service.delete_book(book_id)

        checkpointer = await infra.postgres.service.init_checkpointer()
        for run_id, run_manifest in manifests.items():
            await checkpointer.adelete_thread(
                keys.thread_id(
                    book_id,
                    run_manifest["extraction_run_id"],
                    run_id,
                    run_manifest["workflow_version"],
                    run_manifest["processed_book_revision"],
                    run_manifest.get("qdrant_index_profile_fingerprint"),
                    run_manifest.get("checkpoint_generation", 0),
                )
            )

        pointer_key = ingestion.source.storage.keys.current_retrieval_index_key(book_id)
        if await storage.exists(pointer_key):
            await storage.delete(pointer_key)

        objects_deleted = 0
        for run_id in run_ids:
            if not domain.safe_segment(run_id):
                continue
            objects_deleted += await storage.delete_prefix(
                ingestion.source.storage.keys.agentic_retrieval_index_prefix(book_id, run_id),
            )
            await redis.delete(keys.cancel_key(book_id, run_id))
            await settings.llm_counter.service.delete_run(
                "bd",
                keys.usage_run_id(book_id, run_id),
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
