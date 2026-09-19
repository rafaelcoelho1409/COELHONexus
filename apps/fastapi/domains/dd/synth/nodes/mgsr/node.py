"""mgsr_replan — LangGraph node; all orchestration in service.mgsr_replan_run."""
from __future__ import annotations
import domains
from domains.dd.synth.runtime.observability.service import traced

from . import service


@traced("mgsr_replan")
async def mgsr_replan(state: domains.dd.synth.state.SynthState) -> dict:
    return await service.mgsr_replan_run(state)
