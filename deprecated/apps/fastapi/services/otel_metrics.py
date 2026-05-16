"""
KD-specific custom OpenTelemetry metrics — emitted into the same meter
configured by services.otel_setup, so they land in Mimir (via Alloy) alongside
LiteLLM's auto-emitted per-deployment metrics.

These complement what LiteLLM emits (latency, tokens, cost per deployment) by
adding KD-pipeline-specific signals that LiteLLM has no awareness of:

  kd.chapter_synth_duration_seconds   Histogram (s)   Per-chapter wall-clock
  kd.chapter_outcome_total            Counter         Accept / DEBT / OP-12 RESCUE
  kd.refiner_iters_to_accept          Histogram       Convergence speed
  kd.bucket_split_overflow_total      Counter         How often Phase A.5 cap is hit
  kd.classical_grader_dim_score       Histogram       Per-dim grader scores (0-1)
  kd.audit_missing_hashes_ratio       Histogram       0.0-1.0 hash-drop rate per iter
  kd.study_completion_seconds         Histogram       End-to-end wall-clock
  kd.classical_patch_applied_total    Counter         Phase 4 classical refiner patches

Each metric carries labels (attributes) that make slicing useful:
  - framework (FastAPI / Docker / Terragrunt / …)
  - kd_process (section_synth / grader / refiner / curator / critic / summary)
  - chapter_number (where applicable)
  - outcome (accept / debt / op12_rescue / regenerate)
  - deployment_id (for routing-decision queries)
  - pinned_model (which model the chapter was pinned to, for Fix #2 analysis)

Production query examples (PromQL):

  # Which pinned model converges fastest?
  histogram_quantile(0.5,
    sum by (le, pinned_model) (
      rate(kd_refiner_iters_to_accept_bucket{outcome="accept"}[1h])
    )
  )

  # Which (framework, process) gets the most audit-fail drops?
  rate(kd_audit_missing_hashes_ratio_sum[5m])
    /
  rate(kd_audit_missing_hashes_ratio_count[5m])

  # Bucket-split overflow rate per framework
  sum by (framework) (rate(kd_bucket_split_overflow_total[1h]))
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy singleton — instruments created on first access after init_otel ran.
_instruments: dict = {}


def _ensure_instruments():
    """Create all KD metric instruments (idempotent)."""
    if _instruments:
        return _instruments
    try:
        from services.otel_setup import get_meter
        meter = get_meter()

        _instruments["chapter_synth_duration"] = meter.create_histogram(
            name="kd.chapter_synth_duration_seconds",
            description="Per-chapter synth wall-clock from start to accept/debt",
            unit="s",
        )
        _instruments["chapter_outcome"] = meter.create_counter(
            name="kd.chapter_outcome_total",
            description="Chapter outcomes — labels: outcome ∈ {accept, debt_below, "
                        "op12_rescue}; pinned_model; framework",
        )
        _instruments["refiner_iters"] = meter.create_histogram(
            name="kd.refiner_iters_to_accept",
            description="Number of Self-Refine iters before accept (or budget exhaustion)",
            unit="1",
        )
        _instruments["bucket_split_overflow"] = meter.create_counter(
            name="kd.bucket_split_overflow_total",
            description="Times Phase A.5 hit the section-count cap and merged "
                        "overflow into 'Additional'",
        )
        _instruments["grader_dim_score"] = meter.create_histogram(
            name="kd.classical_grader_dim_score",
            description="Per-dim classical grader score (0-1) — labels: dim ∈ "
                        "{signal_to_noise, code_density, ...}",
            unit="1",
        )
        _instruments["audit_missing_ratio"] = meter.create_histogram(
            name="kd.audit_missing_hashes_ratio",
            description="Per-iter ratio of missing vault hashes (0-1)",
            unit="1",
        )
        _instruments["study_completion_duration"] = meter.create_histogram(
            name="kd.study_completion_seconds",
            description="End-to-end study wall-clock (ingest → assembler)",
            unit="s",
        )
        _instruments["classical_patch_applied"] = meter.create_counter(
            name="kd.classical_patch_applied_total",
            description="Phase 4 classical refiner patch applications — labels: dim",
        )
        logger.info(f"[otel-metrics] {len(_instruments)} KD metric instruments registered")
    except Exception as e:
        logger.warning(f"[otel-metrics] init failed: {type(e).__name__}: {e}")
    return _instruments


def record_chapter_outcome(
    *, outcome: str, framework: str, chapter_number: int,
    pinned_model: str | None = None, duration_s: float | None = None,
    iterations: int | None = None,
):
    """Record outcome + duration + iters for one chapter's synth lifecycle."""
    inst = _ensure_instruments()
    attrs = {
        "framework": framework,
        "chapter_number": str(chapter_number),
        "outcome": outcome,
    }
    if pinned_model:
        attrs["pinned_model"] = pinned_model
    try:
        if "chapter_outcome" in inst:
            inst["chapter_outcome"].add(1, attributes=attrs)
        if duration_s is not None and "chapter_synth_duration" in inst:
            inst["chapter_synth_duration"].record(duration_s, attributes=attrs)
        if iterations is not None and "refiner_iters" in inst:
            inst["refiner_iters"].record(iterations, attributes=attrs)
    except Exception:
        pass


def record_bucket_split_overflow(*, framework: str, sections_dropped: int):
    """Increment when Phase A.5 hits the section cap."""
    inst = _ensure_instruments()
    try:
        if "bucket_split_overflow" in inst:
            inst["bucket_split_overflow"].add(
                1,
                attributes={
                    "framework": framework,
                    "sections_dropped": str(sections_dropped),
                },
            )
    except Exception:
        pass


def record_grader_dim_score(*, framework: str, dim: str, score: float):
    """Record one grader dim's score (0-1)."""
    inst = _ensure_instruments()
    try:
        if "grader_dim_score" in inst:
            inst["grader_dim_score"].record(
                score,
                attributes={"framework": framework, "dim": dim},
            )
    except Exception:
        pass


def record_audit_missing(*, framework: str, chapter_number: int,
                         iteration: int, missing_ratio: float):
    """Record the audit's missing-hash ratio for an iter."""
    inst = _ensure_instruments()
    try:
        if "audit_missing_ratio" in inst:
            inst["audit_missing_ratio"].record(
                missing_ratio,
                attributes={
                    "framework": framework,
                    "chapter_number": str(chapter_number),
                    "iteration": str(iteration),
                },
            )
    except Exception:
        pass


def record_study_completion(*, framework: str, duration_s: float,
                            n_accepted: int, n_total: int, outcome: str):
    """Record end-to-end study completion (called from assembler node)."""
    inst = _ensure_instruments()
    try:
        if "study_completion_duration" in inst:
            inst["study_completion_duration"].record(
                duration_s,
                attributes={
                    "framework": framework,
                    "outcome": outcome,
                    "accepted_count": str(n_accepted),
                    "total_chapters": str(n_total),
                },
            )
    except Exception:
        pass


def record_classical_patch(*, dim: str, framework: str):
    """Increment when Phase 4 classical refiner applies a patch."""
    inst = _ensure_instruments()
    try:
        if "classical_patch_applied" in inst:
            inst["classical_patch_applied"].add(
                1,
                attributes={"dim": dim, "framework": framework},
            )
    except Exception:
        pass
