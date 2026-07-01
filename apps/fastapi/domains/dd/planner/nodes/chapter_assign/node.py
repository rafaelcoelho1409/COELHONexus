"""chapter_assign node shell — multi-assignment with lexical fallback so no doc is silently dropped."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import chapter_assign_run


@traced("chapter_assign")
async def chapter_assign(state: PlannerState) -> dict:
    return await chapter_assign_run(state)
