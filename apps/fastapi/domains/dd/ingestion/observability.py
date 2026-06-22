"""DD ingestion metric recorders."""
from __future__ import annotations

from infra.otel.metrics import get_instrument


def record_ingestion_run(
    *,
    framework: str,
    tier_kind: str,
    outcome: str,
    duration_s: float | None = None,
    output_files: int | None = None,
    output_bytes: int | None = None,
) -> None:
    attrs = {
        "framework": framework,
        "tier_kind": tier_kind or "unknown",
        "outcome":   outcome or "unknown",
    }
    try:
        if (inst := get_instrument("ingestion_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := get_instrument("ingestion_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if output_files is not None and (
            inst := get_instrument("ingestion_output_files")
        ) is not None:
            inst.record(max(output_files, 0), attributes = attrs)
        if output_bytes is not None and (
            inst := get_instrument("ingestion_output_bytes")
        ) is not None:
            inst.record(max(output_bytes, 0), attributes = attrs)
    except Exception:
        pass
