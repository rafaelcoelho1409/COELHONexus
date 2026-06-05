"""Substep 7.5 — order_chapters: LangGraph node shell.

Pedagogical chapter ordering via LLM + USC vote (Bundle 8, 2026-05-25).
Sits between `chapter_select` and `plan_write`. Replaces the arbitrary
chapter_select order with an LLM-driven pedagogical ordering (Borda
aggregation) and a deterministic foundational-prefix rule.

All orchestration lives in service.order_chapters_run.
"""
from __future__ import annotations

from ..observability import traced
from ..state import PlannerState

from .service import order_chapters_run


@traced("order_chapters")
async def order_chapters(state: PlannerState) -> dict:
    return await order_chapters_run(state)
