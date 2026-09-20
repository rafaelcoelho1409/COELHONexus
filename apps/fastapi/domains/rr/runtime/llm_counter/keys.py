"""Redis key builders for RR's per-scan LLM counter."""
from __future__ import annotations


def phase_field_prefix(phase: str) -> str:
    return f"phase:{phase}"


def counters_key(scan_id: str) -> str:
    return f"rr:{scan_id}:llm:counters"


def models_key(scan_id: str, phase: str) -> str:
    return f"rr:{scan_id}:llm:models:{phase}"
