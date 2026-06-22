"""Research Radar metric recorders."""
from __future__ import annotations

from infra.otel.metrics import get_instrument


def record_scan_run(
    *,
    degraded: bool,
    outcome: str,
    duration_s: float | None = None,
    findings: int | None = None,
    candidates: int | None = None,
    theme_count: int | None = None,
) -> None:
    attrs = {
        "outcome":  outcome or "unknown",
        "degraded": str(bool(degraded)).lower(),
    }
    try:
        if (inst := get_instrument("rr_scan_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := get_instrument("rr_scan_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if findings is not None and (inst := get_instrument("rr_findings")) is not None:
            inst.record(max(findings, 0), attributes = attrs)
        if candidates is not None and (
            inst := get_instrument("rr_candidates")
        ) is not None:
            inst.record(max(candidates, 0), attributes = attrs)
        if theme_count is not None and (
            inst := get_instrument("rr_theme_count")
        ) is not None:
            inst.record(max(theme_count, 0), attributes = attrs)
    except Exception:
        pass


def record_phase_event(*, phase: str) -> None:
    try:
        if (inst := get_instrument("rr_phase_event_total")) is not None:
            inst.add(1, attributes = {"phase": phase or "unknown"})
    except Exception:
        pass
