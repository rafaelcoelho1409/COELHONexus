"""checklist_eval — LangGraph node."""
from __future__ import annotations
import domains
from domains.dd.synth.runtime.observability.service import traced
from . import service


@traced("checklist_eval")
async def checklist_eval(state: domains.dd.synth.state.SynthState) -> dict:
    return await service.checklist_eval_run(state)
