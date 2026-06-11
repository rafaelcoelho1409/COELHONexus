"""ycs/agents — Pydantic request schemas for the agentic-RAG endpoints.

Direct port of deprecated `schemas/youtube/inputs.py:L16-22, L124-138, L143-176`.
Field shapes are verbatim deprecated; ConfigDict + NonEmptyStr come from
the ycs/content schemas module (already shipped Wave 1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from domains.ycs.content.schemas import NonEmptyStr


# =============================================================================
# LLM Configuration
# =============================================================================
class LLMConfig(BaseModel):
    """User-provided LLM config persisted to Redis JSON
    `coelhonexus:youtube:agents:config`. Strict port — even though YCS's
    NIM key is now BYOK in the rotator, deprecated did this verbatim."""
    provider:    NonEmptyStr        = "NVIDIA"
    model:       NonEmptyStr | None = None
    temperature: float | None       = None
    base_url:    NonEmptyStr | None = None
    api_key:     NonEmptyStr | None = None
    model_config = ConfigDict(extra = "allow")


# =============================================================================
# Agentic RAG Requests
# =============================================================================
class RAGSearchRequest(BaseModel):
    """Adaptive Agentic RAG question.

    Modes (auto-detected by classifier, or forced via `force_mode`):
      - fast: simple → direct LLM answer, no retrieval (<2s)
      - standard: factual → full RAG with citations (15-60s)
      - deep: analytical → multi-agent research synthesis (30-120s)"""
    question:    NonEmptyStr
    # `thread_id` accepts the soft-empty sentinels ("" or "default") the
    # handler treats as "no thread, no cache, no history" — Pydantic's
    # NonEmptyStr would 422 on "" and the frontend has to special-case it.
    thread_id:   str                                      = "default"
    max_retries: int                                      = 3
    force_mode:  Literal["fast", "standard", "deep"] | None = None
    # Scope to specific channels (auto-detected from question if not provided)
    channel_ids: list[NonEmptyStr] | None                 = None


# =============================================================================
# Ingestion (Phase 2)
# =============================================================================
class IngestRequest(BaseModel):
    """Request to ingest transcripts from ES into Qdrant.
    If `video_ids` is None, ingests ALL transcripts in ES."""
    video_ids:     list[NonEmptyStr] | None = None
    chunk_size:    int                      = 2000
    chunk_overlap: int                      = 200


# =============================================================================
# Knowledge Graph (Phase 3)
# =============================================================================
class GraphIngestRequest(BaseModel):
    """Request to extract entities from full transcripts into Neo4j.
    If `video_ids` is None, processes ALL transcripts in ES.
    `batch_size` controls concurrent LLM calls per batch."""
    video_ids:  list[NonEmptyStr] | None = None
    batch_size: int                      = 3


# =============================================================================
# Full Pipeline (Celery chain: extract → Qdrant → Neo4j)
# =============================================================================
class PipelineRequest(BaseModel):
    """Full channel pipeline: extract → ingest vectors → ingest graph."""
    channel_id:            NonEmptyStr
    max_results:           int  = 0
    include_transcription: bool = True
    include_qdrant:        bool = True
    include_graph:         bool = False
