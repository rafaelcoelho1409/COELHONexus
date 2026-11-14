"""Durable operational contract for book-scoped retrieval indexing."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from . import versions

SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]
Sha256Hex = Annotated[str, StringConstraints(pattern = r"^[0-9a-f]{64}$")]


class RagIndexManifest(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "2.0", "3.0"] = "3.0"
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
    processed_book_key: NonEmptyText | None = None
    processed_book_sha256: Sha256Hex | None = None
    processed_book_revision: int | None = Field(default = None, ge = 1, strict = True)
    processed_page_count: int = Field(default = 0, ge = 0, strict = True)
    point_count: int = Field(default = 0, ge = 0, strict = True)
    graph_node_count: int = Field(default = 0, ge = 0, strict = True)
    graph_relationship_count: int = Field(default = 0, ge = 0, strict = True)
    created_at: float = Field(gt = 0)
    updated_at: float = Field(gt = 0)
    finished_at: float | None = None
    error: str | None = None

    @model_validator(mode = "after")
    def _validate_processed_book_pin(self) -> RagIndexManifest:
        if self.schema_version == "3.0" and any(value is None for value in (
            self.processed_book_key,
            self.processed_book_sha256,
            self.processed_book_revision,
        )):
            raise ValueError("3.0 retrieval runs must pin their processed-book revision")
        return self
