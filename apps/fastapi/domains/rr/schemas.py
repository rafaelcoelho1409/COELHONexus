"""Pydantic boundary schemas for the RR domain.

Per docs/CODE-CONVENTIONS.md §2: Pydantic at HTTP + Celery + LLM
boundaries. Internal value objects (NormalizedPaper, Extraction, Finding)
live in entities.py as plain dataclasses.

These schemas are used by:
  - api/v1/rr/scan/router.py    — HTTP boundary (POST body, GET response)
  - domains/rr/task.py          — Celery task argument validation
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .taxonomy import is_valid_vertical


# --------------------------------------------------------------------------- #
# POST /scan — the trigger
# --------------------------------------------------------------------------- #
class ScanRequest(BaseModel):
    """Body for POST /v1/rr/scan. Captures the operator's intent for ONE
    radar pass. Validated by FastAPI at the HTTP boundary."""

    profile_id: str = Field(
        default = "default",
        description = (
            "Profile identifier — partitions the seen-set in radar_seen so "
            "the digest's 'New since last scan' section is per-profile. "
            "'default' is the single-user catch-all."
        ),
        min_length = 1,
        max_length = 64,
    )
    topic: str = Field(
        ...,
        description = (
            "Topical query (2-8 words). Threaded into every discovery "
            "subagent's query — e.g. 'deep agents', 'constrained decoding', "
            "'kalman filters in trading'."
        ),
        min_length = 1,
        max_length = 200,
    )
    verticals: list[str] = Field(
        default_factory = list,
        description = (
            "Vertical categories used by signal_score.vertical_fit, e.g. "
            "['cs.LG', 'cs.AI'] for ML, ['q-fin.PR', 'q-fin.ST'] for "
            "quant finance. Empty = no vertical filter. Every entry must be "
            "a valid arXiv subject code; the picker UI's client-side check "
            "and this server-side validator share the same taxonomy "
            "(domains/rr/taxonomy.py)."
        ),
    )

    @field_validator("verticals")
    @classmethod
    def _validate_verticals(cls, v: list[str]) -> list[str]:
        """Strip + drop empties + reject unknown codes. Defense-in-depth
        against a stale/forged client payload reaching the agent with junk
        categories that would silently zero `vertical_fit` for every paper."""
        cleaned = [s.strip() for s in v if s and s.strip()]
        bad = [c for c in cleaned if not is_valid_vertical(c)]
        if bad:
            raise ValueError(
                f"Invalid arXiv subject codes: {bad!r}. "
                "See https://arxiv.org/category_taxonomy for the full list."
            )
        return cleaned

    top_n: int = Field(
        default = 12,
        ge = 4,
        le = 100,
        description = (
            "How many papers from triage to deep-read. 12 = sweet spot for "
            "single-pass scans. 30-100 supported for bulk runs — note that "
            "the phase-enforcer budget (MAX_CORRECTIONS=20) and inline "
            "backfill cap (BACKFILL_MAX=3) were calibrated against the "
            "8-12 envelope; at N>30 expect partial-extractions degradation "
            "until those caps are tuned in a follow-up."
        ),
    )


class ScanCreated(BaseModel):
    """Response for POST /v1/rr/scan. Returned immediately — the Celery task
    runs asynchronously; clients should subscribe to SSE events for progress."""

    scan_id:    UUID
    task_id:    str
    status:     str = Field(description = "Always 'pending' at creation time.")
    started_at: datetime


# --------------------------------------------------------------------------- #
# GET /scan/{id} — the status + findings read
# --------------------------------------------------------------------------- #
class ScanResult(BaseModel):
    """Response for GET /v1/rr/scan/{id}. status='done' callers also get the
    full findings array; earlier statuses get just the lifecycle metadata."""

    scan_id:          UUID
    profile_id:       str
    status:           str
    started_at:       datetime
    finished_at:      datetime | None       = None
    total_candidates: int                   = 0
    total_in_digest:  int                   = 0
    error:            str | None            = None
    findings:         list[dict[str, Any]]  = Field(
        default_factory = list,
        description = (
            "Denormalized digest items (the radar_findings.digest_json "
            "payload). Empty unless status='done'. Each item has "
            "{arxiv_id, rank, signal, title, authors, summary, themes, "
            "sources, is_new, extraction}."
        ),
    )
    digest_minio_key: str | None = Field(
        default = None,
        description = (
            "MinIO key for the canonical digest.json artifact (e.g. "
            "'rr/scans/{scan_id}/digest.json'). Survives Postgres "
            "truncation."
        ),
    )
