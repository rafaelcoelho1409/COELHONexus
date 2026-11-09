"""Durable operational contract for book-scoped retrieval indexing."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import versions

SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


class RagIndexManifest(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "2.0"] = "2.0"
    workflow_version: str = versions.WORKFLOW_VERSION
    graph_extraction_prompt_version: str = versions.GRAPH_EXTRACTION_PROMPT_VERSION
    book_id: SafeSegment
    distill_run_id: SafeSegment
    extraction_run_id: SafeSegment
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    vector_status: Literal["pending", "running", "done", "failed", "cancelled"] = "pending"
    graph_status: Literal["pending", "running", "done", "failed", "cancelled"] = "pending"
    current_stage: Literal["preparing", "vector_index", "graph_materialization", "complete"] | None = None
    cancel_requested: bool = False
    attempt: int = Field(ge = 1, strict = True)
    collection_name: NonEmptyText | None = None
    embedding_model: NonEmptyText | None = None
    embedding_dimensions: int | None = Field(default = None, ge = 1, strict = True)
    source_page_count: int = Field(default = 0, ge = 0, strict = True)
    learning_unit_count: int = Field(default = 0, ge = 0, strict = True)
    guide_record_count: int = Field(default = 0, ge = 0, strict = True)
    point_count: int = Field(default = 0, ge = 0, strict = True)
    graph_node_count: int = Field(default = 0, ge = 0, strict = True)
    graph_relationship_count: int = Field(default = 0, ge = 0, strict = True)
    created_at: float = Field(gt = 0)
    updated_at: float = Field(gt = 0)
    finished_at: float | None = None
    error: str | None = None
