from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen = True, slots = True)
class MetricSpec:
    """One OTel instrument declaration. `key` is the internal cache name used
    by record_* helpers; `name` is the wire name that lands in Mimir."""
    key:         str
    name:        str
    description: str
    kind:        Literal["counter", "histogram"]
    unit:        str = ""


# All KD-pipeline-specific metrics. Adding a new metric is one entry here —
# `_ensure_instruments` builds them all via a single loop. Labels are attached
# at record time by the record_* helpers in service.py.
INSTRUMENTS: tuple[MetricSpec, ...] = (
    MetricSpec(
        key         = "chapter_synth_duration",
        name        = "kd.chapter_synth_duration_seconds",
        description = "Per-chapter synth wall-clock from start to accept/debt",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "chapter_outcome",
        name        = "kd.chapter_outcome_total",
        description = ("Chapter outcomes — labels: outcome ∈ {accept, debt_below, "
                       "op12_rescue}; pinned_model; framework"),
        kind        = "counter",
    ),
    MetricSpec(
        key         = "refiner_iters",
        name        = "kd.refiner_iters_to_accept",
        description = "Number of Self-Refine iters before accept (or budget exhaustion)",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "bucket_split_overflow",
        name        = "kd.bucket_split_overflow_total",
        description = ("Times Phase A.5 hit the section-count cap and merged "
                       "overflow into 'Additional'"),
        kind        = "counter",
    ),
    MetricSpec(
        key         = "grader_dim_score",
        name        = "kd.classical_grader_dim_score",
        description = ("Per-dim classical grader score (0-1) — labels: dim ∈ "
                       "{signal_to_noise, code_density, ...}"),
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "audit_missing_ratio",
        name        = "kd.audit_missing_hashes_ratio",
        description = "Per-iter ratio of missing vault hashes (0-1)",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "study_completion_duration",
        name        = "kd.study_completion_seconds",
        description = "End-to-end study wall-clock (ingest → assembler)",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "classical_patch_applied",
        name        = "kd.classical_patch_applied_total",
        description = "Phase 4 classical refiner patch applications — labels: dim",
        kind        = "counter",
    ),
)
