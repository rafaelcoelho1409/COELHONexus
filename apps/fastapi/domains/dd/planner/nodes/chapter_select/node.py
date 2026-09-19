"""chapter_select node shell — greedy coverage with orphan protection; legacy reduce_node output schema for transparent downstream reads."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced

from . import service


@traced("chapter_select")
async def chapter_select(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.chapter_select_run(state)
