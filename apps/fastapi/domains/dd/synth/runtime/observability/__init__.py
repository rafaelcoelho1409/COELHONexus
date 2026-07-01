"""Synth observability — OTel span helpers (service.py) + pipeline metrics (metrics.py); instruments in infra.otel.metrics_registry."""
from __future__ import annotations

from .metrics import (
    record_audit_missing,
    record_bucket_split_overflow,
    record_chapter_outcome,
    record_classical_patch,
    record_grader_dim_score,
    record_study_completion,
)
from .service import attach_span_attrs, traced


__all__ = [
    "attach_span_attrs",
    "traced",
    "record_chapter_outcome",
    "record_bucket_split_overflow",
    "record_grader_dim_score",
    "record_audit_missing",
    "record_study_completion",
    "record_classical_patch",
]
