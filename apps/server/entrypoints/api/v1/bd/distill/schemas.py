"""Wire contracts for Book Distiller Distill lifecycle routes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class StartRequest(BaseModel):
    ingestion_ai_run_id: str | None = None


class Capabilities(BaseModel):
    execution_enabled: bool


class RunStart(BaseModel):
    status: Literal["queued"]
    book_id: str
    extraction_run_id: str
    ingestion_ai_run_id: str
    run_id: str


class CancelResponse(BaseModel):
    book_id: str
    run_id: str
    status: str


class WipeResponse(BaseModel):
    book_id: str
    run_id: str
    wiped: bool
    objects_deleted: int


class RagIndexStatus(BaseModel):
    book_id: str
    distill_run_id: str
    extraction_run_id: str
    status: Literal["not_started", "queued", "running", "done", "failed"]
    attempt: int = 0
    collection_name: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    source_page_count: int = 0
    learning_unit_count: int = 0
    guide_record_count: int = 0
    point_count: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
