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
