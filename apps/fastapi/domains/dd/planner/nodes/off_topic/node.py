"""Substep 3 — off_topic: LangGraph node shell.

Pure LLM-as-judge KEEP/DROP per page (2026-05-17 night — supersedes the
percentile cut + cosine cleave). Cosine margins stay in stats as
telemetry only (operator can correlate margin to LLM verdict to spot
calibration drift).

All orchestration lives in service.off_topic_run.
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import off_topic_run


@traced("off_topic")
async def off_topic(state: PlannerState) -> dict:
    return await off_topic_run(state)
