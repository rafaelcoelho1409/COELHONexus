"""YCS Ask metric recorders."""
from __future__ import annotations

from infra.otel.metrics import get_instrument


def record_ask_run(
    *,
    route: str,
    mode: str,
    outcome: str,
    grounded: bool | None = None,
    duration_s: float | None = None,
    citation_count: int | None = None,
) -> None:
    attrs = {
        "route":   route or "unknown",
        "mode":    mode or "unknown",
        "outcome": outcome or "unknown",
    }
    try:
        if (inst := get_instrument("ycs_ask_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := get_instrument("ycs_ask_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if citation_count is not None and (
            inst := get_instrument("ycs_citation_count")
        ) is not None:
            inst.record(max(citation_count, 0), attributes = attrs)
        if grounded and (inst := get_instrument("ycs_grounded_total")) is not None:
            inst.add(1, attributes = attrs)
    except Exception:
        pass


def record_retrieved_docs(*, route: str, mode: str, count: int) -> None:
    try:
        if (inst := get_instrument("ycs_retrieved_docs")) is not None:
            inst.record(
                max(count, 0),
                attributes = {"route": route or "unknown", "mode": mode or "unknown"},
            )
    except Exception:
        pass


def record_graded_docs(*, route: str, mode: str, count: int) -> None:
    try:
        if (inst := get_instrument("ycs_graded_docs")) is not None:
            inst.record(
                max(count, 0),
                attributes = {"route": route or "unknown", "mode": mode or "unknown"},
            )
    except Exception:
        pass


def record_rewrite(*, route: str, mode: str) -> None:
    try:
        if (inst := get_instrument("ycs_rewrite_total")) is not None:
            inst.add(
                1,
                attributes = {"route": route or "unknown", "mode": mode or "unknown"},
            )
    except Exception:
        pass


def record_subquestion(*, route: str, outcome: str) -> None:
    try:
        if (inst := get_instrument("ycs_subquestion_total")) is not None:
            inst.add(
                1,
                attributes = {
                    "route":   route or "unknown",
                    "outcome": outcome or "unknown",
                },
            )
    except Exception:
        pass
