"""chapter_propose node shell — corpus-adaptive chapter count proposal."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import chapter_propose_run


@traced("chapter_propose")
async def chapter_propose(state: PlannerState) -> dict:
    return await chapter_propose_run(state)
