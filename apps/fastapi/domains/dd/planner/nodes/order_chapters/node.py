"""order_chapters node shell — Borda-aggregated LLM ordering + foundational-prefix rule between chapter_select and plan_write."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import order_chapters_run


@traced("order_chapters")
async def order_chapters(state: PlannerState) -> dict:
    return await order_chapters_run(state)
