"""DD ingestion observability — metric recorder. No shared span decorator here
(unlike planner/synth) — ingestion has exactly one dispatcher call site
(`runtime/dispatch/service.py:run`), so its span is inline there."""
from __future__ import annotations
import infra
from . import params


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
        "tier_kind": tier_kind or params.UNKNOWN,
        "outcome":   outcome or params.UNKNOWN,
    }
    try:
        if (inst := infra.opentelemetry.service.get_instrument(params.INSTRUMENT_RUN_TOTAL)) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := infra.opentelemetry.service.get_instrument(params.INSTRUMENT_RUN_DURATION)
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if output_files is not None and (
            inst := infra.opentelemetry.service.get_instrument(params.INSTRUMENT_OUTPUT_FILES)
        ) is not None:
            inst.record(max(output_files, 0), attributes = attrs)
        if output_bytes is not None and (
            inst := infra.opentelemetry.service.get_instrument(params.INSTRUMENT_OUTPUT_BYTES)
        ) is not None:
            inst.record(max(output_bytes, 0), attributes = attrs)
    except Exception:  # noqa: BLE001, S110 — metrics are best-effort and must never fail a run
        pass
