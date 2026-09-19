"""chapter_propose node shell — corpus-adaptive chapter count proposal."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced

from . import service


@traced("chapter_propose")
async def chapter_propose(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.chapter_propose_run(state)
