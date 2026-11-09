"""Wire contracts for book retrieval lifecycle routes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RagCapabilities(BaseModel):
    execution_enabled: bool


class RagIndexStatus(BaseModel):
    book_id: str
    distill_run_id: str
    extraction_run_id: str
    status: Literal["not_started", "queued", "running", "done", "failed", "cancelled"]
    workflow_version: str = "1.0"
    graph_extraction_prompt_version: str = "2"
    vector_status: Literal["pending", "running", "done", "failed", "cancelled"] = "pending"
    graph_status: Literal["pending", "running", "done", "failed", "cancelled"] = "pending"
    current_stage: Literal["preparing", "vector_index", "graph_materialization", "complete"] | None = None
    cancel_requested: bool = False
    attempt: int = 0
    collection_name: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    source_page_count: int = 0
    learning_unit_count: int = 0
    guide_record_count: int = 0
    point_count: int = 0
    graph_node_count: int = 0
    graph_relationship_count: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    finished_at: float | None = None
    error: str | None = None


class RagWipeResponse(BaseModel):
    book_id: str
    distill_run_id: str
    wiped: bool
    objects_deleted: int
