"""Durable status contract for one processed-book retrieval index."""
from __future__ import annotations
from .. import params, versions
from ... import source

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
ObjectKey = Annotated[str, StringConstraints(min_length = 1)]
Sha256Hex = Annotated[str, StringConstraints(pattern = r"^[0-9a-f]{64}$")]
NodeStatus = Literal["pending", "running", "done", "failed", "cancelled"]
Neo4jStage = Literal[
    "idle",
    "extracting",
    "building_graph",
    "clearing_graph",
    "preparing_writes",
    "writing_nodes",
    "writing_relationships",
    "complete",
]


class RetrievalIndexManifest(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = versions.SCHEMA_VERSION
    workflow_version: str = versions.WORKFLOW_VERSION
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    agentic_run_id: SafeSegment
    processed_book_key: ObjectKey
    processed_book_sha256: Sha256Hex
    processed_book_revision: int = Field(ge = 1, strict = True)
    processed_page_count: int = Field(ge = 1, strict = True)
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    cancel_requested: bool = False
    replay_from_node: str | None = None
    current_node: str | None = None
    node_status: dict[str, NodeStatus]
    attempt: int = Field(ge = 1, strict = True)
    checkpoint_generation: int = Field(default = 0, ge = 0, strict = True)
    source_artifact_key: ObjectKey | None = None
    pin_sources_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    elasticsearch_index: str | None = None
    elasticsearch_document_count: int = Field(default = 0, ge = 0, strict = True)
    elasticsearch_passage_index: str | None = None
    elasticsearch_passage_count: int = Field(default = 0, ge = 0, strict = True)
    elasticsearch_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    qdrant_collection: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default = None, ge = 1, strict = True)
    qdrant_index_profile_version: str = source.indexes.qdrant_index.params.QDRANT_INDEX_PROFILE_VERSION
    qdrant_chunking_version: str = source.chunking.params.QDRANT_CHUNKING_VERSION
    qdrant_chunk_size_chars: int = Field(default = source.chunking.params.CHUNK_SIZE, ge = 1, strict = True)
    qdrant_chunk_overlap_chars: int = Field(default = source.chunking.params.CHUNK_OVERLAP, ge = 0, strict = True)
    qdrant_chunk_separators: list[str] = Field(
        default_factory = lambda: list(source.chunking.params.QDRANT_CHUNK_SEPARATORS),
    )
    qdrant_sparse_model: str = source.indexes.qdrant_index.params.SPARSE_MODEL_NAME
    qdrant_index_profile_fingerprint: str = ""
    qdrant_point_count: int = Field(default = 0, ge = 0, strict = True)
    qdrant_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    previous_qdrant_collection: str | None = None
    previous_agentic_run_id: SafeSegment | None = None
    previous_processed_book_revision: int | None = Field(default = None, ge = 1, strict = True)
    previous_qdrant_index_profile_fingerprint: str | None = None
    neo4j_node_count: int = Field(default = 0, ge = 0, strict = True)
    neo4j_relationship_count: int = Field(default = 0, ge = 0, strict = True)
    graph_batches_completed: int = Field(default = 0, ge = 0, strict = True)
    graph_batches_total: int = Field(default = 0, ge = 0, strict = True)
    graph_batches_empty_count: int = Field(default = 0, ge = 0, strict = True)
    graph_batches_entity_only_count: int = Field(default = 0, ge = 0, strict = True)
    graph_extraction_split_count: int = Field(default = 0, ge = 0, strict = True)
    neo4j_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    neo4j_llm_calls_active: int | None = Field(default = None, ge = 0, strict = True)
    neo4j_stage: Neo4jStage = "idle"
    neo4j_stage_completed: int = Field(default = 0, ge = 0, strict = True)
    neo4j_stage_total: int = Field(default = 0, ge = 0, strict = True)
    neo4j_node_write_batches_completed: int = Field(default = 0, ge = 0, strict = True)
    neo4j_node_write_batches_total: int = Field(default = 0, ge = 0, strict = True)
    neo4j_relationship_write_batches_completed: int = Field(default = 0, ge = 0, strict = True)
    neo4j_relationship_write_batches_total: int = Field(default = 0, ge = 0, strict = True)
    created_at: float = Field(gt = 0)
    updated_at: float = Field(gt = 0)
    finished_at: float | None = None
    error: str | None = None

    @staticmethod
    def initial_node_status() -> dict[str, NodeStatus]:
        return {node: "pending" for node in params.NODE_ORDER}
