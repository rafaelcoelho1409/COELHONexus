"""Inspect and dispatch safe migrations of published Book Distiller vectors."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import domains
import infra
import domains.bd.rag.embedding_migration.task

from . import keys, params

logger = logging.getLogger(__name__)


async def get_state(redis: Any) -> dict[str, Any] | None:
    try:
        raw = await redis.get(keys.STATE_KEY)
        state = json.loads(raw) if raw else None
    except Exception:
        return None
    return state if isinstance(state, dict) else None


async def write_state(redis: Any, state: dict[str, Any]) -> None:
    await redis.set(keys.STATE_KEY, json.dumps(state), ex = params.STATE_TTL_S)


async def update_state(redis: Any, **values: Any) -> dict[str, Any] | None:
    state = await get_state(redis)
    if state is None:
        return None
    state.update(values, updated_at = time.time())
    await write_state(redis, state)
    return state


async def _book_ids(storage: infra.minio.service.MinIOStorage) -> list[str]:
    keys = domains.bd.ingestion.source.storage.keys
    book_ids = set(await storage.list_subfolders(keys.books_prefix()))
    book_ids.update(await storage.list_subfolders(keys.PREVIOUS_BOOKS_PREFIX))
    book_ids.update(await storage.list_subfolders(keys.LEGACY_BOOKS_PREFIX))
    return sorted(book_ids)


async def _current_indexes(
    storage: infra.minio.service.MinIOStorage,
) -> tuple[list[dict[str, Any]], int]:
    book_ids = await _book_ids(storage)
    gate = asyncio.Semaphore(params.BOOK_CONCURRENCY)

    async def read_one(book_id: str) -> tuple[dict[str, Any] | None, bool]:
        async with gate:
            try:
                published = await domains.bd.rag.ai.runs.service.read_current_index(book_id, storage = storage)
            except Exception:
                logger.exception("[bd-embedding-migration] unable to inspect book %s", book_id)
                return None, True
        if published is None:
            return None, False
        manifest = published.get("index")
        if not isinstance(manifest, dict):
            return None, True
        profile = {**manifest, **published}
        collection = profile.get("qdrant_collection")
        model = profile.get("embedding_model")
        dimensions = profile.get("embedding_dimensions")
        prefix = domains.bd.rag.source.indexes.qdrant_index.params.QDRANT_COLLECTION_PREFIX
        if (
            not isinstance(collection, str)
            or not (collection == prefix or collection.startswith(f"{prefix}__"))
            or not isinstance(model, str)
            or type(dimensions) is not int
            or dimensions < 1
        ):
            return None, True
        if type(manifest.get("qdrant_point_count")) is not int or manifest["qdrant_point_count"] < 1:
            return None, False
        return {
            "book_id": book_id,
            "agentic_run_id": profile.get("agentic_run_id"),
            "processed_book_revision": profile.get("processed_book_revision"),
            "processed_book_sha256": profile.get("processed_book_sha256"),
            "collection": collection,
            "model": model,
            "dimensions": dimensions,
            "index_profile_fingerprint": profile.get("qdrant_index_profile_fingerprint", ""),
        }, False

    results = await asyncio.gather(*(read_one(book_id) for book_id in book_ids))
    return [item for item, _ in results if item is not None], sum(failed for _, failed in results)


async def _embedding_profile() -> tuple[str, int]:
    vector, metadata = await domains.settings.embeddings.service.embed_probe_async()
    dimensions = len(vector)
    model = str(metadata.get("model") or domains.settings.embeddings.service.get_configured_model()).strip()
    if not model or dimensions < 1:
        raise ValueError("the embedding endpoint returned no model or vector dimensions")
    return model, dimensions


async def inspect_status(redis: Any | None = None) -> dict[str, Any]:
    """Return the tested target collection and every current book that needs re-embedding."""
    state = await get_state(redis) if redis is not None else None
    try:
        storage = infra.minio.service.get_storage()
        indexes, inventory_errors = await _current_indexes(storage)
    except Exception:
        logger.exception("[bd-embedding-migration] unable to inspect the published book indexes")
        indexes, inventory_errors = [], 1
    collections = sorted({str(item["collection"]) for item in indexes})
    try:
        model, dimensions = await _embedding_profile()
    except Exception as error:
        logger.info("[bd-embedding-migration] embedding connection check failed: %s", type(error).__name__)
        return {
            "connected": False,
            "connection_error": "The embedding connection could not be verified.",
            "target_model": None,
            "target_dimensions": None,
            "target_collection": None,
            "active_collection": collections[0] if len(collections) == 1 else None,
            "books_indexed": len(indexes),
            "book_count": 0,
            "inventory_errors": inventory_errors,
            "mismatches": [],
            "needed": False,
            "state": state,
        }

    target_collection = domains.bd.rag.source.indexes.qdrant_index.domain.collection_name(model, dimensions)
    stale = [item for item in indexes if (
        item["model"] != model
        or item["dimensions"] != dimensions
        or item["collection"] != target_collection
    )]
    if state and state.get("status") != "running":
        if (
            state.get("to_model") != model
            or state.get("to_dimensions") != dimensions
            or (state.get("status") == "complete" and stale)
            or (state.get("status") == "failed" and not stale)
        ):
            state = None
    groups: dict[tuple[str, int], int] = {}
    for item in stale:
        source_model = str(item["model"])
        source_dimensions = item["dimensions"]
        key = (source_model, source_dimensions if type(source_dimensions) is int else 0)
        groups[key] = groups.get(key, 0) + 1

    return {
        "connected": True,
        "connection_error": None,
        "target_model": model,
        "target_dimensions": dimensions,
        "target_collection": target_collection,
        "active_collection": target_collection if indexes else (collections[0] if len(collections) == 1 else None),
        "books_indexed": len(indexes),
        "book_count": len(stale),
        "inventory_errors": inventory_errors,
        "mismatches": [
            {"from_model": source_model, "from_dimensions": source_dimensions, "book_count": count}
            for (source_model, source_dimensions), count in sorted(groups.items())
        ],
        "needed": bool(stale),
        "state": state,
    }


async def start_migration(redis: Any) -> dict[str, Any]:
    """Queue a resumable, additive re-embedding of every currently published book index."""
    async with redis.lock(keys.LOCK_KEY, timeout = 120, blocking_timeout = 30):
        status = await inspect_status(redis)
        existing = status.get("state")
        if existing and existing.get("status") in {"queued", "running"}:
            if not status["connected"]:
                return existing
            if (
                existing.get("to_model") == status["target_model"]
                and existing.get("to_dimensions") == status["target_dimensions"]
            ):
                return existing
            raise ValueError(
                "A migration to a different embedding profile is already running. Wait for it to finish, then retry."
            )
        if not status["connected"]:
            raise ValueError("Test the embedding connection successfully before migrating books.")
        if status["inventory_errors"]:
            raise ValueError(
                "Book Distiller could not inspect every published index. Retry after the storage issue is resolved."
            )
        if not status["needed"]:
            raise ValueError("No Book Distiller embedding migration is needed.")

        storage = infra.minio.service.get_storage()
        indexes, inventory_errors = await _current_indexes(storage)
        if inventory_errors:
            raise ValueError(
                "Book Distiller could not inspect every published index. Retry after the storage issue is resolved."
            )
        target_model = status["target_model"]
        target_dimensions = status["target_dimensions"]
        target_collection = status["target_collection"]
        books = [item for item in indexes if (
            item["model"] != target_model
            or item["dimensions"] != target_dimensions
            or item["collection"] != target_collection
        )]
        if not books:
            raise ValueError("No Book Distiller embedding migration is needed.")

        state: dict[str, Any] = {
            "status": "queued",
            "from_models": status["mismatches"],
            "to_model": target_model,
            "to_dimensions": target_dimensions,
            "target_collection": target_collection,
            "total": len(books),
            "current": 0,
            "completed": 0,
            "failed": 0,
            "failed_book_ids": [],
            "current_book": None,
            "inventory_errors": inventory_errors,
            "task_id": "",
            "started_at": time.time(),
            "updated_at": time.time(),
        }
        await write_state(redis, state)
        try:
            result = domains.bd.rag.embedding_migration.task.reembed_books.apply_async(
                args = (target_model, target_dimensions, [item["book_id"] for item in books]),
            )
        except Exception:
            state.update(status = "failed", error = "The migration could not be queued.")
            await write_state(redis, state)
            raise
        latest = await get_state(redis) or state
        latest["task_id"] = result.id
        latest.setdefault("status", "queued")
        await write_state(redis, latest)
        logger.info(
            "[bd-embedding-migration] queued %d books for %s (%s)",
            len(books), target_model, target_collection,
        )
        return latest
