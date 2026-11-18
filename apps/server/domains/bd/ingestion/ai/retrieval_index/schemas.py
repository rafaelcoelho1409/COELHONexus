"""Durable status contract for one processed-book retrieval index."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import params, versions

SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
ObjectKey = Annotated[str, StringConstraints(min_length = 1)]
Sha256Hex = Annotated[str, StringConstraints(pattern = r"^[0-9a-f]{64}$")]
NodeStatus = Literal["pending", "running", "done", "failed", "cancelled"]


class RetrievalIndexManifest(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0"] = versions.SCHEMA_VERSION
    workflow_version: str = versions.WORKFLOW_VERSION
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    agentic_run_id: SafeSegment
    processed_book_key: ObjectKey
    processed_book_sha256: Sha256Hex
    processed_book_revision: int = Field(ge = 1, strict = True)
    processed_page_count: int = Field(ge = 1, strict = True)
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    replay_from_node: str | None = None
    current_node: str | None = None
    node_status: dict[str, NodeStatus]
    attempt: int = Field(ge = 1, strict = True)
    source_artifact_key: ObjectKey | None = None
    pin_sources_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    elasticsearch_index: str | None = None
    elasticsearch_document_count: int = Field(default = 0, ge = 0, strict = True)
    elasticsearch_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    qdrant_collection: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default = None, ge = 1, strict = True)
    qdrant_point_count: int = Field(default = 0, ge = 0, strict = True)
    qdrant_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    neo4j_node_count: int = Field(default = 0, ge = 0, strict = True)
    neo4j_relationship_count: int = Field(default = 0, ge = 0, strict = True)
    graph_batches_completed: int = Field(default = 0, ge = 0, strict = True)
    graph_batches_total: int = Field(default = 0, ge = 0, strict = True)
    neo4j_pages_completed: int = Field(default = 0, ge = 0, strict = True)
    created_at: float = Field(gt = 0)
    updated_at: float = Field(gt = 0)
    finished_at: float | None = None
    error: str | None = None

    @classmethod
    def initial_node_status(cls) -> dict[str, NodeStatus]:
        return {node: "pending" for node in params.NODE_ORDER}
