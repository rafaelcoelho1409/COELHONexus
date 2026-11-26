"""Stable, storage-key-free MCP contracts for BD Agentic Ingestion."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal["queued", "running", "done", "failed", "cancelled"]
CancelStatus = Literal["cancelling", "queued", "running", "done", "failed", "cancelled"]


class IngestionRunSummary(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    book_id: str
    agentic_run_id: str
    extraction_run_id: str
    workflow_version: str
    status: RunStatus
    current_node: str | None = None
    node_status: dict[str, str] = Field(default_factory = dict)
    expected_page_count: int = Field(ge = 0)
    processed_page_count: int = Field(ge = 0)
    review_page_count: int = Field(ge = 0)
    unverified_page_count: int = Field(ge = 0)
    selected_page_count: int | None = Field(default = None, ge = 0)
    excluded_page_count: int | None = Field(default = None, ge = 0)
    quality_status: str | None = None
    replay_from_node: str | None = None
    created_at: float
    updated_at: float
    finished_at: float | None = None
    error: str | None = None


class IngestionQualitySummary(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    book_id: str
    agentic_run_id: str
    status: RunStatus
    quality_status: str | None = None
    expected_page_count: int = Field(ge = 0)
    delivered_page_count: int = Field(ge = 0)
    discarded_blank_page_count: int = Field(ge = 0)
    review_page_count: int = Field(ge = 0)
    unverified_page_count: int = Field(ge = 0)
    visual_failed_count: int = Field(ge = 0)
    quality_report_available: bool
    updated_at: float


class IngestionCommandResult(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    status: CancelStatus
    book_id: str
    agentic_run_id: str
    extraction_run_id: str
    replay_from_node: str | None = None
    message: str | None = None
