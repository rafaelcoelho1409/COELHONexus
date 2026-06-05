"""KD-pipeline-specific OpenTelemetry metrics — emitted to the same meter
configured by core.otel so they land in Mimir (via Alloy) alongside
LiteLLM's auto-emitted per-deployment metrics.

Adds KD signals LiteLLM has no awareness of: chapter outcomes, refiner
convergence speed, bucket-split overflow, per-dim grader scores, audit hash-
drop rate, study completion, classical refiner patches. Labels include
framework, dd_process, chapter_number, outcome, deployment_id, pinned_model.

PromQL slice examples:
    histogram_quantile(0.5, sum by (le, pinned_model)
        (rate(kd_refiner_iters_to_accept_bucket{outcome="accept"}[1h])))
    rate(kd_audit_missing_hashes_ratio_sum[5m])
        / rate(kd_audit_missing_hashes_ratio_count[5m])
    sum by (framework) (rate(kd_bucket_split_overflow_total[1h]))
"""
from __future__ import annotations

import logging

from core.otel import get_meter

from .config import INSTRUMENTS


logger = logging.getLogger(__name__)


# Lazy singleton — instruments created on first record_* call after init_otel ran.
_instruments: dict = {}


def _ensure_instruments() -> dict:
    """Create all KD metric instruments (idempotent). Data-driven from
    config.INSTRUMENTS — one MetricSpec entry → one OTel instrument."""
    if _instruments:
        return _instruments
    try:
        meter = get_meter()
        for spec in INSTRUMENTS:
            kwargs = {"name": spec.name, "description": spec.description}
            if spec.unit:
                kwargs["unit"] = spec.unit
            factory = (meter.create_counter if spec.kind == "counter"
                       else meter.create_histogram)
            _instruments[spec.key] = factory(**kwargs)
        logger.info(f"[otel-metrics] {len(_instruments)} KD metric instruments registered")
    except Exception as e:
        logger.warning(f"[otel-metrics] init failed: {type(e).__name__}: {e}")
    return _instruments


def record_chapter_outcome(
    *,
    outcome: str,
    framework: str,
    chapter_number: int,
    pinned_model: str | None = None,
    duration_s: float | None = None,
    iterations: int | None = None,
) -> None:
    """Outcome + duration + iters for one chapter's synth lifecycle."""
    inst = _ensure_instruments()
    attrs = {
        "framework":      framework,
        "chapter_number": str(chapter_number),
        "outcome":        outcome,
    }
    if pinned_model:
        attrs["pinned_model"] = pinned_model
    try:
        if "chapter_outcome" in inst:
            inst["chapter_outcome"].add(1, attributes = attrs)
        if duration_s is not None and "chapter_synth_duration" in inst:
            inst["chapter_synth_duration"].record(duration_s, attributes = attrs)
        if iterations is not None and "refiner_iters" in inst:
            inst["refiner_iters"].record(iterations, attributes = attrs)
    except Exception:
        pass


def record_bucket_split_overflow(*, framework: str, sections_dropped: int) -> None:
    """Increment when Phase A.5 hits the section cap."""
    inst = _ensure_instruments()
    try:
        if "bucket_split_overflow" in inst:
            inst["bucket_split_overflow"].add(
                1,
                attributes = {
                    "framework":        framework,
                    "sections_dropped": str(sections_dropped),
                },
            )
    except Exception:
        pass


def record_grader_dim_score(*, framework: str, dim: str, score: float) -> None:
    """One grader dim's score (0-1)."""
    inst = _ensure_instruments()
    try:
        if "grader_dim_score" in inst:
            inst["grader_dim_score"].record(
                score, attributes = {"framework": framework, "dim": dim},
            )
    except Exception:
        pass


def record_audit_missing(
    *,
    framework: str,
    chapter_number: int,
    iteration: int,
    missing_ratio: float,
) -> None:
    """Audit's missing-hash ratio for one iter."""
    inst = _ensure_instruments()
    try:
        if "audit_missing_ratio" in inst:
            inst["audit_missing_ratio"].record(
                missing_ratio,
                attributes = {
                    "framework":      framework,
                    "chapter_number": str(chapter_number),
                    "iteration":      str(iteration),
                },
            )
    except Exception:
        pass


def record_study_completion(
    *,
    framework: str,
    duration_s: float,
    n_accepted: int,
    n_total: int,
    outcome: str,
) -> None:
    """End-to-end study completion (called from the assembler node)."""
    inst = _ensure_instruments()
    try:
        if "study_completion_duration" in inst:
            inst["study_completion_duration"].record(
                duration_s,
                attributes = {
                    "framework":      framework,
                    "outcome":        outcome,
                    "accepted_count": str(n_accepted),
                    "total_chapters": str(n_total),
                },
            )
    except Exception:
        pass


def record_classical_patch(*, dim: str, framework: str) -> None:
    """Increment when Phase 4 classical refiner applies a patch."""
    inst = _ensure_instruments()
    try:
        if "classical_patch_applied" in inst:
            inst["classical_patch_applied"].add(
                1, attributes = {"dim": dim, "framework": framework},
            )
    except Exception:
        pass
