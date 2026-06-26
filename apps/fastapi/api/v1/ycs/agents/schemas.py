"""ycs/agents — Pydantic request schemas for the agentic-RAG endpoints."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from domains.ycs.content.schemas import NonEmptyStr


# LLM Configuration
class LLMConfig(BaseModel):
    """User-provided LLM config persisted to Redis JSON. `api_key`/`base_url` accepted but ignored
    at build time — keys are owned by the global Settings page and resolved via `resolve_key()`."""
    provider:    NonEmptyStr        = "nim"
    model:       NonEmptyStr | None = None
    temperature: float | None       = None
    base_url:    NonEmptyStr | None = None   # ignored at build time
    api_key:     NonEmptyStr | None = None   # ignored at build time
    model_config = ConfigDict(extra = "allow")


# Agentic RAG Requests
class RAGSearchRequest(BaseModel):
    """Adaptive RAG question. `force_mode` overrides auto-classification (fast/standard/deep)."""
    question:    NonEmptyStr
    # `thread_id` accepts the soft-empty sentinels ("" or "default") the
    # handler treats as "no thread, no cache, no history" — Pydantic's
    # NonEmptyStr would 422 on "" and the frontend has to special-case it.
    thread_id:   str                                      = "default"
    max_retries: int                                      = 3
    force_mode:  Literal["fast", "standard", "deep"] | None = None
    channel_ids: list[NonEmptyStr] | None                 = None
    # Two-pass DEEP protocol: preview_plan=True → emit plan early (SSE) and halt before fan-out;
    # sub_questions=[...] on second request → use caller-supplied plan, skip LLM plan call.
    preview_plan:  bool                            = False
    sub_questions: list[NonEmptyStr] | None        = None


# Ingestion
class IngestRequest(BaseModel):
    """Request to ingest transcripts from ES into Qdrant.
    If `video_ids` is None, ingests ALL transcripts in ES."""
    video_ids:     list[NonEmptyStr] | None = None
    chunk_size:    int                      = 2000
    chunk_overlap: int                      = 200


# Knowledge Graph
class GraphIngestRequest(BaseModel):
    """Request to extract entities from full transcripts into Neo4j.
    If `video_ids` is None, processes ALL transcripts in ES.
    `batch_size` controls concurrent LLM calls per batch."""
    video_ids:  list[NonEmptyStr] | None = None
    batch_size: int                      = 3


# Full Pipeline (Celery chain: extract → Qdrant → Neo4j)
class PipelineRequest(BaseModel):
    """Full channel pipeline: extract → ingest vectors → ingest graph."""
    channel_id:            NonEmptyStr
    max_results:           int  = 0
    include_transcription: bool = True
    include_qdrant:        bool = True
    include_graph:         bool = False
