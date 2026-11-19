"""Deterministic helpers for retrieval-index run records."""
from __future__ import annotations
from . import schemas
from ... import source

import time
from typing import Any


def safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


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
        "node_status": schemas.RetrievalIndexManifest.initial_node_status(),
        "attempt": attempt,
        "pin_sources_pages_completed": 0,
        "elasticsearch_pages_completed": 0,
        "qdrant_pages_completed": 0,
        "qdrant_index_profile_version": source.indexes.qdrant_index.params.QDRANT_INDEX_PROFILE_VERSION,
        "qdrant_chunking_version": source.chunking.params.QDRANT_CHUNKING_VERSION,
        "qdrant_chunk_size_chars": source.chunking.params.CHUNK_SIZE,
        "qdrant_chunk_overlap_chars": source.chunking.params.CHUNK_OVERLAP,
        "qdrant_chunk_separators": list(source.chunking.params.QDRANT_CHUNK_SEPARATORS),
        "qdrant_sparse_model": source.indexes.qdrant_index.params.SPARSE_MODEL_NAME,
        "qdrant_index_profile_fingerprint": source.indexes.qdrant_index.domain.index_profile_fingerprint(),
        "previous_qdrant_collection": None,
        "previous_agentic_run_id": None,
        "previous_processed_book_revision": None,
        "previous_qdrant_index_profile_fingerprint": None,
        "checkpoint_generation": 0,
        "neo4j_pages_completed": 0,
        "graph_batches_completed": 0,
        "graph_batches_total": 0,
        "neo4j_stage": "idle",
        "neo4j_llm_calls_active": 0,
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
