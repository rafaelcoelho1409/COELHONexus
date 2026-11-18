"""Dense and sparse passage indexing for processed-book pages."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import domains
import infra
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .. import params
from . import domain


_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size = params.CHUNK_SIZE,
    chunk_overlap = params.CHUNK_OVERLAP,
    add_start_index = True,
    separators = ["\n\n", "\n", " ", ""],
)


async def _ensure_collection(client, name: str, dimensions: int) -> None:
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
    for field, schema in (
        ("book_id", "keyword"),
        ("agentic_run_id", "keyword"),
        ("processed_book_revision", "integer"),
        ("processed_page_number", "integer"),
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
    try:
        await _ensure_collection(client, collection, dimensions)
        sparse = infra.qdrant.service.FastEmbedSparse(model_name = params.SPARSE_MODEL_NAME)
        chunks: list[dict[str, Any]] = []
        remaining_chunks: dict[int, int] = {}
        for row in rows:
            for chunk_index, document in enumerate(_SPLITTER.create_documents([row["content"]])):
                chunks.append({
                    "id": domain.point_id(
                        book_id,
                        agentic_run_id,
                        revision,
                        row["processed_page_number"],
                        chunk_index,
                        document.page_content,
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
