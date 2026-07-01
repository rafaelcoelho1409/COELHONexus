"""checklist_eval — LangGraph node."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import checklist_eval_run


@traced("checklist_eval")
async def checklist_eval(state: SynthState) -> dict:
    return await checklist_eval_run(state)
