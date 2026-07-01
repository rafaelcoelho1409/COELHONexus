"""chapter_select node shell — greedy coverage with orphan protection; legacy reduce_node output schema for transparent downstream reads."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import chapter_select_run


@traced("chapter_select")
async def chapter_select(state: PlannerState) -> dict:
    return await chapter_select_run(state)
