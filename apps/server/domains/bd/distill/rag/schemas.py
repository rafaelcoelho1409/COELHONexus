"""Durable operational contract for post-publication retrieval indexing."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


class RagIndexManifest(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0"] = "1.0"
    book_id: SafeSegment
    distill_run_id: SafeSegment
    extraction_run_id: SafeSegment
    status: Literal["queued", "running", "done", "failed"]
    attempt: int = Field(ge = 1, strict = True)
    collection_name: NonEmptyText | None = None
    embedding_model: NonEmptyText | None = None
    embedding_dimensions: int | None = Field(default = None, ge = 1, strict = True)
    source_page_count: int = Field(default = 0, ge = 0, strict = True)
    learning_unit_count: int = Field(default = 0, ge = 0, strict = True)
    guide_record_count: int = Field(default = 0, ge = 0, strict = True)
    point_count: int = Field(default = 0, ge = 0, strict = True)
    created_at: float = Field(gt = 0)
    updated_at: float = Field(gt = 0)
    finished_at: float | None = None
    error: str | None = None
