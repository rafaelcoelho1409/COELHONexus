"""chapter_assign node shell — multi-assignment with lexical fallback so no doc is silently dropped."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced
from . import service


@traced("chapter_assign")
async def chapter_assign(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.chapter_assign_run(state)
