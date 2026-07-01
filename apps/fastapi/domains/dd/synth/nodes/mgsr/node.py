"""mgsr_replan — LangGraph node; all orchestration in service.mgsr_replan_run."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import mgsr_replan_run


@traced("mgsr_replan")
async def mgsr_replan(state: SynthState) -> dict:
    return await mgsr_replan_run(state)
