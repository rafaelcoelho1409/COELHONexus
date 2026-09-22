"""sawc_derive — LangGraph node; all orchestration in service.sawc_derive_run."""
from __future__ import annotations
import domains
from domains.dd.synth.runtime.observability.service import traced
from . import service


@traced("sawc_derive")
async def sawc_derive(state: domains.dd.synth.state.SynthState) -> dict:
    return await service.sawc_derive_run(state)
