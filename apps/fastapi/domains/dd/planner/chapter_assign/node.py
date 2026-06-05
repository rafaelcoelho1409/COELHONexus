"""Substep 6 — chapter_assign: LangGraph node shell.

Per-doc LLM assigns confidence scores to each chapter. Multi-assignment
allowed (a doc can score >threshold on multiple chapters; chapter_select
breaks ties via coverage greedy). Lexical fallback on per-doc LLM
failure (Fix, 2026-05-30) so no doc gets silently dropped.

All orchestration lives in service.chapter_assign_run.

State writes:
  chapter_doc_assignments_ref — MinIO key of the JSON
  assign_stats                — counts, coverage stats
"""
from __future__ import annotations

from ..observability import traced
from ..state import PlannerState

from .service import chapter_assign_run


@traced("chapter_assign")
async def chapter_assign(state: PlannerState) -> dict:
    return await chapter_assign_run(state)
