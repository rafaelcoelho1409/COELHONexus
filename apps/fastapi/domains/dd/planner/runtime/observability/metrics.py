"""Planner pipeline metric recorders."""
from __future__ import annotations

from infra.otel.metrics import get_instrument


def record_planner_run(
    *,
    framework: str,
    mode: str,
    outcome: str,
    duration_s: float | None = None,
    chapter_count: int | None = None,
) -> None:
    attrs = {
        "framework": framework,
        "mode":      mode or "unknown",
        "outcome":   outcome or "unknown",
    }
    try:
        if (inst := get_instrument("planner_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := get_instrument("planner_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if chapter_count is not None and (
            inst := get_instrument("planner_chapter_count")
        ) is not None:
            inst.record(max(chapter_count, 0), attributes = attrs)
    except Exception:
        pass
