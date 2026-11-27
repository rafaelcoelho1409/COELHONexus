"""Stable, storage-key-free MCP contracts for BD Agentic RAG indexing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal["queued", "running", "done", "failed", "cancelled"]
CancelStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class RetrievalIndexSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    workflow_version: str
    status: RunStatus
    current_node: str | None = None
    node_status: dict[str, str] = Field(default_factory=dict)
    attempt: int = Field(ge=1)
    processed_book_revision: int = Field(ge=1)
    processed_page_count: int = Field(ge=1)
    pin_sources_pages_completed: int = Field(ge=0)
    elasticsearch_document_count: int = Field(ge=0)
    elasticsearch_passage_count: int = Field(ge=0)
    elasticsearch_pages_completed: int = Field(ge=0)
    qdrant_point_count: int = Field(ge=0)
    qdrant_pages_completed: int = Field(ge=0)
    neo4j_node_count: int = Field(ge=0)
    neo4j_relationship_count: int = Field(ge=0)
    neo4j_pages_completed: int = Field(ge=0)
    neo4j_stage: str
    neo4j_stage_completed: int = Field(ge=0)
    neo4j_stage_total: int = Field(ge=0)
    replay_from_node: str | None = None
    cancel_requested: bool
    created_at: float
    updated_at: float
    finished_at: float | None = None
    error: str | None = None


class CurrentIndexSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    status: Literal["done"]
    run_status: RunStatus
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1)
    attempt: int | None = Field(default=None, ge=1)
    processed_book_revision: int = Field(ge=1)
    published_at: float | None = None
    elasticsearch_document_count: int = Field(ge=0)
    elasticsearch_passage_count: int = Field(ge=0)
    qdrant_point_count: int = Field(ge=0)
    neo4j_node_count: int = Field(ge=0)
    neo4j_relationship_count: int = Field(ge=0)


class IndexCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CancelStatus
    book_id: str
    agentic_run_id: str
    attempt: int = Field(ge=1)
    replay_from_node: str | None = None
    message: str | None = None
