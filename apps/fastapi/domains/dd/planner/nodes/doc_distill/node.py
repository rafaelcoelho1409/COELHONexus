"""Substep 4 — doc_distill: LangGraph node shell.

Per-doc semantic representation for the LLM-first planner. Pass-through
for ≤80 docs; parallel LLM distillation otherwise, with deterministic
fallback on per-doc LLM failure (Fix #4).

All orchestration lives in service.doc_distill_run.

State writes:
  doc_distill_ref   — MinIO key of the JSON ({key → DocDistillate, ...})
  doc_distill_stats — counts + cache_hit + wall_ms
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import doc_distill_run


@traced("doc_distill")
async def doc_distill(state: PlannerState) -> dict:
    return await doc_distill_run(state)
