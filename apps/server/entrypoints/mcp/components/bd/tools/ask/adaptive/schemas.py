"""Stable structured output for one-shot BD Ask calls."""
from __future__ import annotations
from ... import ingestion

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AskMode = Literal["fast", "standard", "deep"]
PipelineState = Literal[
    "ready",
    "reindex_required",
    "indexing",
    "ingesting",
    "awaiting_ingestion",
    "not_extracted",
    "failed",
]


class AskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    indexed_run_id: str
    answer: str
    mode: AskMode
    citations: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    retrieval_sources: list[str] = Field(default_factory=list, max_length=8)
    retrieval_source_status: dict[str, str] = Field(default_factory=dict)
    external_search_status: str | None = None
    external_search_provider: str | None = None
    confidence_score: float | None = None
    verification_status: str | None = None
    duration_ms: int = Field(ge=0)
    usage: dict[str, Any] | None = None


class PipelineReadiness(BaseModel):
    """Stable output for current source, ingestion, and retrieval readiness."""

    model_config = ConfigDict(extra="forbid")

    book_id: str
    state: PipelineState
    ready: bool
    queryable: bool
    extraction_status: str | None = None
    extraction_run_id: str | None = None
    ingestion_run: ingestion.book_processing.schemas.IngestionRunSummary | None = None
    index_run: ingestion.rag_indexing.schemas.RetrievalIndexSummary | None = None
    current_index: ingestion.rag_indexing.schemas.CurrentIndexSummary | None = None
    configured_embedding_model: str
    embedding_profile_matches: bool | None = None
    warnings: list[str] = Field(default_factory=list)
    updated_at: float | None = None
