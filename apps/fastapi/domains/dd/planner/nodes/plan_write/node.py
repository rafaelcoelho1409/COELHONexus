"""Substep 9 — plan_write: LangGraph node shell.

Persists the FINAL chapter plan to MinIO. Per
`docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md` + May 2026 SOTA references
(SurveyGen-I + SurveyForge + LLMxMapReduce-V2 + TnT-LLM + Atlas/SLSA
provenance idioms).

All orchestration lives in service.plan_write_run.

State writes:
  plan_path — MinIO key of the LATEST pointer (consumer-facing key)
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import plan_write_run


@traced("plan_write")
async def plan_write(state: PlannerState) -> dict:
    return await plan_write_run(state)
