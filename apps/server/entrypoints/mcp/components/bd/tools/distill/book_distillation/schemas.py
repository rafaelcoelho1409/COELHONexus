"""Stable MCP contracts for BD Distill operations and retrieval."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class DistillNodeProgress(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    completed_count: int = Field(default = 0, ge = 0)
    total_count: int = Field(default = 0, ge = 0)
    active_count: int = Field(default = 0, ge = 0)


class DistillFailureSummary(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    node: str | None = None
    task: str | None = None
    error_type: str
    provider_kind: Literal[
        "unknown",
        "timeout",
        "rate_limit",
        "auth_error",
        "content_filter",
        "server_error",
    ] = "unknown"
    status_code: int | None = None
    failed_at: float


class DistillRunSummary(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    book_id: str
    run_id: str
    extraction_run_id: str
    ingestion_ai_run_id: str
    workflow_version: str
    prompt_version: str
    status: RunStatus
    current_node: str | None = None
    current_task: str | None = None
    node_status: dict[str, str] = Field(default_factory = dict)
    node_progress: dict[str, DistillNodeProgress] = Field(default_factory = dict)
    completed_extractions: int = Field(default = 0, ge = 0)
    completed_units: int = Field(default = 0, ge = 0)
    total_units: int = Field(default = 0, ge = 0)
    completed_chapter_guides: int = Field(default = 0, ge = 0)
    completed_chapter_visuals: int = Field(default = 0, ge = 0)
    completed_chapters: int = Field(default = 0, ge = 0)
    total_chapters: int = Field(default = 0, ge = 0)
    research_enabled: bool = False
    report_available: bool = False
    review_required: bool = True
    created_at: float
    updated_at: float
    finished_at: float | None = None
    error: str | None = None
    failure: DistillFailureSummary | None = None


class DistillCommandResult(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    status: RunStatus
    book_id: str
    run_id: str
    extraction_run_id: str
    ingestion_ai_run_id: str
    replay_from_node: str | None = None
    message: str | None = None


RetrievalStatus = Literal[
    "complete",
    "no_results",
    "disabled",
    "invalid_query",
    "unavailable",
    "timeout",
]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    id: str = Field(min_length = 1, max_length = 240)
    text: str = Field(max_length = 3000)
    score: float = 0.0
    source: str = Field(min_length = 1, max_length = 80)
    citation: dict[str, Any] = Field(default_factory = dict)
    matched_sources: list[str] = Field(default_factory = list)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    status: RetrievalStatus
    source: str = Field(min_length = 1, max_length = 80)
    query: str = Field(max_length = 1000)
    evidence: list[EvidenceRecord] = Field(default_factory = list, max_length = 8)
    error_type: str | None = Field(default = None, max_length = 100)
