"""order_chapters node shell — Borda-aggregated LLM ordering + foundational-prefix rule between chapter_select and plan_write."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced
from . import service


@traced("order_chapters")
async def order_chapters(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.order_chapters_run(state)
