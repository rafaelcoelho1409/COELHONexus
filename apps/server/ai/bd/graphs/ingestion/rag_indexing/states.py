"""Compact state for indexing one immutable processed-book revision."""
from __future__ import annotations

from typing import TypedDict


class ProcessedBookIndexState(TypedDict, total = False):
    """Index identities, artifact references, and backend counts shared by graph nodes."""

    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    replay_from_node: str
    processed_book_key: str
    processed_book_sha256: str
    processed_book_revision: int
    processed_page_count: int
    source_artifact_key: str
    pin_sources_pages_completed: int
    elasticsearch_index: str
    elasticsearch_document_count: int
    elasticsearch_passage_index: str
    elasticsearch_passage_count: int
    elasticsearch_pages_completed: int
    qdrant_collection: str
    embedding_model: str
    embedding_dimensions: int
    qdrant_point_count: int
    qdrant_pages_completed: int
    neo4j_node_count: int
    neo4j_relationship_count: int
    graph_batches_empty_count: int
    graph_batches_entity_only_count: int
    graph_extraction_split_count: int
    neo4j_pages_completed: int
    neo4j_stage: str
    neo4j_stage_completed: int
    neo4j_stage_total: int
    neo4j_node_write_batches_completed: int
    neo4j_node_write_batches_total: int
    neo4j_relationship_write_batches_completed: int
    neo4j_relationship_write_batches_total: int
    status: str
