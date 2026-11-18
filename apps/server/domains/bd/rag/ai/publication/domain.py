"""Validate retrieval-index state and construct the published pointer."""
from __future__ import annotations
from ... import source

from typing import Any


def published_pointer(
    state: dict[str, Any],
    *,
    current_processed_book: tuple[Any, str, str],
    manifest_key: str,
    published_at: float,
) -> dict[str, Any]:
    if state.get("elasticsearch_document_count") != state.get("processed_page_count"):
        raise ValueError("the full-text index does not contain every processed page")
    if state.get("elasticsearch_passage_count", 0) < 1:
        raise ValueError("the full-text passage index contains no processed-book passages")
    if state.get("elasticsearch_passage_count") != state.get("qdrant_point_count"):
        raise ValueError("the Elasticsearch and Qdrant passage counts do not match")
    if state.get("qdrant_point_count", 0) < 1:
        raise ValueError("the vector index contains no processed-book passages")
    if state.get("neo4j_node_count", 0) < state.get("processed_page_count", 0) + 1:
        raise ValueError("the knowledge graph does not contain every processed page")

    processed, processed_key, processed_sha256 = current_processed_book
    if (
        processed.selection_revision != state["processed_book_revision"]
        or processed_key != state["processed_book_key"]
        or processed_sha256 != state["processed_book_sha256"]
    ):
        raise ValueError("the processed-book revision changed while its retrieval index was being built")

    return {
        "book_id": state["book_id"],
        "extraction_run_id": state["extraction_run_id"],
        "agentic_run_id": state["agentic_run_id"],
        "processed_book_revision": state["processed_book_revision"],
        "processed_book_key": processed_key,
        "processed_book_sha256": processed_sha256,
        "manifest_key": manifest_key,
        "elasticsearch_index": state["elasticsearch_index"],
        "elasticsearch_passage_index": state["elasticsearch_passage_index"],
        "elasticsearch_passage_count": state["elasticsearch_passage_count"],
        "qdrant_collection": state["qdrant_collection"],
        "embedding_model": state["embedding_model"],
        "embedding_dimensions": state["embedding_dimensions"],
        "qdrant_index_profile_version": source.indexes.qdrant_index.params.QDRANT_INDEX_PROFILE_VERSION,
        "qdrant_chunking_version": source.chunking.params.QDRANT_CHUNKING_VERSION,
        "qdrant_chunk_size_chars": source.chunking.params.CHUNK_SIZE,
        "qdrant_chunk_overlap_chars": source.chunking.params.CHUNK_OVERLAP,
        "qdrant_chunk_separators": list(source.chunking.params.QDRANT_CHUNK_SEPARATORS),
        "qdrant_sparse_model": source.indexes.qdrant_index.params.SPARSE_MODEL_NAME,
        "qdrant_index_profile_fingerprint": source.indexes.qdrant_index.domain.index_profile_fingerprint(),
        "published_at": published_at,
    }
