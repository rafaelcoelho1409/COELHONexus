"""Celery worker that writes new book vectors before switching their published pointers."""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time

import domains
import infra.celery

from . import params

logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = params.TASK_NAME,
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
    max_retries = 0,
)
def reembed_books(self, target_model: str, target_dimensions: int, book_ids: list[str]) -> dict:
    async def execute() -> dict:
        migration = importlib.import_module("domains.bd.rag.embedding_migration.service")
        async with infra.redis.service.client() as redis:
            await migration.update_state(redis, status = "running", total = len(book_ids))
            try:
                verified_model, verified_dimensions = await migration._embedding_profile()
            except Exception:
                await migration.update_state(
                    redis,
                    status = "failed",
                    finished_at = time.time(),
                    error = "The embedding connection could not be verified after migration was queued.",
                )
                raise
            if verified_model != target_model or verified_dimensions != target_dimensions:
                await migration.update_state(
                    redis,
                    status = "failed",
                    finished_at = time.time(),
                    error = "The configured embedding model changed after migration was queued.",
                )
                raise ValueError("the configured embedding model changed after migration was queued")
            completed = 0
            failed_ids: list[str] = []
            storage = infra.minio.service.get_storage()

            async def report(book_id: str | None) -> None:
                await migration.update_state(
                    redis,
                    current = completed + len(failed_ids),
                    completed = completed,
                    failed = len(failed_ids),
                    failed_book_ids = failed_ids[:100],
                    current_book = book_id,
                )
                self.update_state(
                    state = "PROGRESS",
                    meta = {
                        "current": completed + len(failed_ids),
                        "total": len(book_ids),
                        "books_completed": completed,
                        "books_total": len(book_ids),
                        "failed_books": len(failed_ids),
                        "current_book": book_id,
                    },
                )

            for current_book in book_ids:
                await report(current_book)
                try:
                    await _migrate_one(
                        current_book,
                        target_model = target_model,
                        target_dimensions = target_dimensions,
                        storage = storage,
                        redis = redis,
                    )
                    completed += 1
                except Exception:
                    logger.exception("[bd-embedding-migration] failed to migrate book %s", current_book)
                    failed_ids.append(current_book)
                await report(None)

            finished_at = time.time()
            if failed_ids:
                await migration.update_state(
                    redis,
                    status = "failed",
                    current = len(book_ids),
                    completed = completed,
                    failed = len(failed_ids),
                    failed_book_ids = failed_ids[:100],
                    current_book = None,
                    finished_at = finished_at,
                    error = f"{len(failed_ids)} book(s) could not be migrated; the rest remain available.",
                )
                raise RuntimeError(f"Book embedding migration failed for {len(failed_ids)} book(s)")
            await migration.update_state(
                redis,
                status = "complete",
                current = len(book_ids),
                completed = completed,
                failed = 0,
                current_book = None,
                finished_at = finished_at,
                error = None,
            )
            return {"status": "complete", "books_migrated": completed, "target_model": target_model}

    return asyncio.run(execute())


async def _migrate_one(
    book_id: str,
    *,
    target_model: str,
    target_dimensions: int,
    storage,
    redis,
) -> None:
    published = await domains.bd.rag.ai.runs.service.read_current_index(book_id, storage = storage)
    if published is None:
        return
    index = {**published["index"], **published}
    if (
        index.get("embedding_model") == target_model
        and index.get("embedding_dimensions") == target_dimensions
        and index.get("qdrant_collection") == domains.bd.rag.source.indexes.qdrant_index.domain.collection_name(
            target_model, target_dimensions,
        )
    ):
        return
    run_id = index.get("agentic_run_id")
    revision = index.get("processed_book_revision")
    expected_sha256 = index.get("processed_book_sha256")
    if not isinstance(run_id, str) or type(revision) is not int or not isinstance(expected_sha256, str):
        raise ValueError("the published book retrieval index is incomplete")

    rows, processed_key, processed_sha256, processed_revision = (
        await domains.bd.rag.source.pin_sources.service.read_current_rows(
            book_id,
            run_id,
            storage,
        )
    )
    if (
        processed_revision != revision
        or processed_sha256 != expected_sha256
        or processed_key != index.get("processed_book_key")
    ):
        raise ValueError("the published processed-book revision changed during migration")
    collection, model, dimensions, point_count = await domains.bd.rag.source.indexes.qdrant_index.service.index_pages(
        rows,
        book_id = book_id,
        agentic_run_id = run_id,
        revision = revision,
        embedding_profile = (target_model, target_dimensions),
    )
    if model != target_model or dimensions != target_dimensions or point_count < 1:
        raise ValueError("the target vector index did not pass its completion checks")
    migration = importlib.import_module("domains.bd.rag.embedding_migration.service")
    verified_model, verified_dimensions = await migration._embedding_profile()
    if verified_model != target_model or verified_dimensions != target_dimensions:
        raise ValueError("the configured embedding model changed while the book was being migrated")

    async with redis.lock(
        domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
        timeout = 120,
        blocking_timeout = 30,
    ):
        current = await domains.bd.rag.ai.runs.service.read_current_index(book_id, storage = storage)
        if current is None:
            raise ValueError("the published book index changed before migration could finish")
        current_index = {**current["index"], **current}
        if (
            current_index.get("agentic_run_id") != run_id
            or current_index.get("processed_book_revision") != revision
            or current_index.get("processed_book_sha256") != expected_sha256
        ):
            raise ValueError("the published book index changed before migration could finish")
        if current_index.get("embedding_model") == target_model and (
            current_index.get("embedding_dimensions") == target_dimensions
            and current_index.get("qdrant_collection") == collection
        ):
            return
        if (
            current_index.get("embedding_model") != index.get("embedding_model")
            or current_index.get("embedding_dimensions") != index.get("embedding_dimensions")
            or current_index.get("qdrant_collection") != index.get("qdrant_collection")
        ):
            raise ValueError("the published embedding index changed during migration")

        manifest = await domains.bd.rag.ai.runs.service.read_index(book_id, run_id, storage = storage)
        if manifest is None:
            raise ValueError("the published retrieval-index manifest disappeared")
        previous_collection = current_index.get("qdrant_collection")
        manifest.update(
            previous_qdrant_collection = previous_collection,
            previous_agentic_run_id = run_id,
            previous_processed_book_revision = revision,
            previous_qdrant_index_profile_fingerprint = current_index.get(
                "qdrant_index_profile_fingerprint",
            ),
            qdrant_collection = collection,
            embedding_model = model,
            embedding_dimensions = dimensions,
            qdrant_point_count = point_count,
            qdrant_pages_completed = len(rows),
            qdrant_index_profile_version = domains.bd.rag.source.indexes.qdrant_index.params.QDRANT_INDEX_PROFILE_VERSION,
            qdrant_chunking_version = domains.bd.rag.source.chunking.params.QDRANT_CHUNKING_VERSION,
            qdrant_chunk_size_chars = domains.bd.rag.source.chunking.params.CHUNK_SIZE,
            qdrant_chunk_overlap_chars = domains.bd.rag.source.chunking.params.CHUNK_OVERLAP,
            qdrant_chunk_separators = list(domains.bd.rag.source.chunking.params.QDRANT_CHUNK_SEPARATORS),
            qdrant_sparse_model = domains.bd.rag.source.indexes.qdrant_index.params.SPARSE_MODEL_NAME,
            qdrant_index_profile_fingerprint = domains.bd.rag.source.indexes.qdrant_index.domain.index_profile_fingerprint(),
        )
        await domains.bd.rag.ai.runs.service.write_manifest(manifest, storage = storage)

        pointer_key = domains.bd.ingestion.source.storage.keys.current_retrieval_index_key(book_id)
        pointer = json.loads(await storage.read_text(pointer_key))
        if not isinstance(pointer, dict):
            raise ValueError("the current retrieval-index pointer is malformed")
        pointer.update(
            qdrant_collection = collection,
            embedding_model = model,
            embedding_dimensions = dimensions,
            qdrant_point_count = point_count,
            qdrant_index_profile_version = manifest["qdrant_index_profile_version"],
            qdrant_chunking_version = manifest["qdrant_chunking_version"],
            qdrant_chunk_size_chars = manifest["qdrant_chunk_size_chars"],
            qdrant_chunk_overlap_chars = manifest["qdrant_chunk_overlap_chars"],
            qdrant_chunk_separators = manifest["qdrant_chunk_separators"],
            qdrant_sparse_model = manifest["qdrant_sparse_model"],
            qdrant_index_profile_fingerprint = manifest["qdrant_index_profile_fingerprint"],
            published_at = time.time(),
        )
        await storage.write(
            pointer_key,
            json.dumps(pointer, ensure_ascii = False, separators = (",", ":"), sort_keys = True),
            content_type = "application/json",
        )
