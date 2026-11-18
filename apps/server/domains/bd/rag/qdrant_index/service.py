"""Dense and sparse passage indexing for processed-book pages."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import time
from typing import Any

import domains
import infra
from .. import chunking, params
from . import domain


async def _ensure_collection(client, name: str, model: str, dimensions: int) -> None:
    models = infra.qdrant.service.models
    if not await client.collection_exists(name):
        await client.create_collection(
            collection_name = name,
            vectors_config = {
                "dense": models.VectorParams(
                    size = dimensions,
                    distance = models.Distance.COSINE,
                    hnsw_config = models.HnswConfigDiff(m = 16, ef_construct = 200),
                ),
            },
            sparse_vectors_config = {
                "sparse": models.SparseVectorParams(index = models.SparseIndexParams(on_disk = False)),
            },
        )
    points, _ = await client.scroll(
        collection_name = name,
        limit = 1,
        with_payload = ["embedding_model", "embedding_dimensions"],
        with_vectors = False,
    )
    if points:
        payload = points[0].payload or {}
        stored_model = payload.get("embedding_model")
        stored_dimensions = payload.get("embedding_dimensions")
        if stored_model and stored_model != model:
            raise ValueError("the target Qdrant collection contains vectors from another embedding model")
        if stored_dimensions and stored_dimensions != dimensions:
            raise ValueError("the target Qdrant collection has a different embedding dimension")
    for field, schema in (
        ("book_id", "keyword"),
        ("agentic_run_id", "keyword"),
        ("processed_book_revision", "integer"),
        ("processed_page_number", "integer"),
        ("index_profile_fingerprint", "keyword"),
    ):
        try:
            await client.create_payload_index(
                collection_name = name,
                field_name = field,
                field_schema = schema,
            )
        except Exception as error:  # noqa: BLE001 — an existing payload index is already ready.
            if "exist" not in str(error).casefold():
                raise


async def index_pages(
    rows: list[dict[str, Any]],
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[str, str, int, int]:
    probe, metadata = await domains.settings.embeddings.service.embed_probe_async()
    dimensions = len(probe)
    model = str(metadata.get("model") or "")
    if dimensions < 1 or not model:
        raise ValueError("the configured embedding endpoint returned no model or vector dimensions")
    collection = domain.collection_name(model, dimensions)
    client = infra.qdrant.service.new_qdrant()
    indexed_at = time.time()
    profile_fingerprint = domain.index_profile_fingerprint()
    try:
        await _ensure_collection(client, collection, model, dimensions)
        sparse = infra.qdrant.service.FastEmbedSparse(model_name = params.SPARSE_MODEL_NAME)
        chunks: list[dict[str, Any]] = []
        remaining_chunks: dict[int, int] = {}
        for row in rows:
            for chunk_index, document in enumerate(chunking.domain.page_passages(row["content"])):
                chunks.append({
                    "id": domain.point_id(
                        book_id,
                        agentic_run_id,
                        revision,
                        row["processed_page_number"],
                        chunk_index,
                        document.page_content,
                        profile_fingerprint,
                    ),
                    "text": document.page_content,
                    "payload": {
                        "book_id": book_id,
                        "extraction_run_id": row["extraction_run_id"],
                        "agentic_run_id": agentic_run_id,
                        "processed_book_revision": revision,
                        "source_type": "processed_page",
                        "processed_page_number": row["processed_page_number"],
                        "source_page_number": row["source_page_number"],
                        "section_path": row["section_path"],
                        "content_role": row["content_role"],
                        "content_sha256": row["content_sha256"],
                        "chunk_index": chunk_index,
                        "source_char_start": document.metadata.get("start_index", 0),
                        "content": document.page_content,
                        "embedding_model": model,
                        "embedding_dimensions": dimensions,
                        "index_profile_version": params.QDRANT_INDEX_PROFILE_VERSION,
                        "chunking_policy_version": params.QDRANT_CHUNKING_VERSION,
                        "chunk_size_chars": params.CHUNK_SIZE,
                        "chunk_overlap_chars": params.CHUNK_OVERLAP,
                        "chunk_separators": list(params.QDRANT_CHUNK_SEPARATORS),
                        "sparse_model": params.SPARSE_MODEL_NAME,
                        "index_profile_fingerprint": profile_fingerprint,
                        "indexed_at": indexed_at,
                    },
                })
                page_number = row["processed_page_number"]
                remaining_chunks[page_number] = remaining_chunks.get(page_number, 0) + 1
            remaining_chunks.setdefault(row["processed_page_number"], 0)
        completed_pages = {page_number for page_number, count in remaining_chunks.items() if count == 0}
        last_reported_page_count = len(completed_pages)
        if progress_callback is not None:
            await progress_callback(len(completed_pages), len(rows))
        point_count = 0
        for offset in range(0, len(chunks), params.EMBED_BATCH_SIZE):
            if cancel_check is not None and await cancel_check():
                raise RuntimeError("processed-book retrieval indexing was cancelled")
            batch = chunks[offset : offset + params.EMBED_BATCH_SIZE]
            texts = [item["text"] for item in batch]
            dense_vectors, used_model = await domains.settings.embeddings.service.embed_texts_async(texts)
            if used_model != model or any(len(vector) != dimensions for vector in dense_vectors):
                raise ValueError("the configured embedding model changed during retrieval indexing")
            sparse_vectors = list(sparse.embed_documents(texts))
            points = [
                infra.qdrant.service.models.PointStruct(
                    id = item["id"],
                    vector = {
                        "dense": dense_vectors[index],
                        "sparse": infra.qdrant.service.models.SparseVector(
                            indices = sparse_vectors[index].indices,
                            values = sparse_vectors[index].values,
                        ),
                    },
                    payload = item["payload"],
                )
                for index, item in enumerate(batch)
            ]
            await client.upsert(collection_name = collection, points = points)
            point_count += len(points)
            for item in batch:
                page_number = item["payload"]["processed_page_number"]
                remaining_chunks[page_number] -= 1
                if remaining_chunks[page_number] == 0:
                    completed_pages.add(page_number)
            completed_page_count = len(completed_pages)
            if progress_callback is not None and (
                completed_page_count - last_reported_page_count >= params.PAGE_PROGRESS_INTERVAL
                or completed_page_count == len(rows)
            ):
                await progress_callback(completed_page_count, len(rows))
                last_reported_page_count = completed_page_count
        return collection, model, dimensions, point_count
    finally:
        await client.close()


async def prune_stale_book_points(
    *,
    collection: str,
    book_id: str,
    current_agentic_run_id: str,
    current_revision: int,
    previous_collection: str | None = None,
    previous_agentic_run_id: str | None = None,
    previous_revision: int | None = None,
    current_profile_fingerprint: str | None = None,
    previous_profile_fingerprint: str | None = None,
) -> int:
    """Remove obsolete snapshots for one book, retaining the published and prior snapshot.

    Cleanup runs after the new index is published. A prior snapshot in a different
    embedding collection remains untouched, preserving model-migration rollback data.
    """
    if not collection or not book_id or not current_agentic_run_id or current_revision < 1:
        raise ValueError("a collection, book, current run, and positive revision are required")

    preserved = {(current_agentic_run_id, current_revision, current_profile_fingerprint)}
    if (
        previous_collection == collection
        and previous_agentic_run_id
        and previous_revision is not None
        and previous_revision > 0
    ):
        preserved.add((previous_agentic_run_id, previous_revision, previous_profile_fingerprint))

    client = infra.qdrant.service.new_qdrant()
    try:
        if not await client.collection_exists(collection):
            return 0
        models = infra.qdrant.service.models
        scroll_filter = models.Filter(must = [
            models.FieldCondition(key = "book_id", match = models.MatchValue(value = book_id)),
        ])
        offset = None
        stale_ids: list[str] = []
        while True:
            points, offset = await client.scroll(
                collection_name = collection,
                scroll_filter = scroll_filter,
                limit = 1000,
                offset = offset,
                with_payload = [
                    "agentic_run_id",
                    "processed_book_revision",
                    "index_profile_fingerprint",
                ],
                with_vectors = False,
            )
            for point in points:
                payload = point.payload or {}
                identity = (
                    payload.get("agentic_run_id"),
                    payload.get("processed_book_revision"),
                    payload.get("index_profile_fingerprint"),
                )
                if identity not in preserved:
                    stale_ids.append(point.id)
            if offset is None:
                break
        for start in range(0, len(stale_ids), 1000):
            await client.delete(
                collection_name = collection,
                points_selector = models.PointIdsList(points = stale_ids[start : start + 1000]),
            )
        return len(stale_ids)
    finally:
        await client.close()
