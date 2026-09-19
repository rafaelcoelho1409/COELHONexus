"""sawc_write — LangGraph node; all orchestration in service.sawc_write_run."""
from __future__ import annotations
import domains
from domains.dd.synth.runtime.observability.service import traced

from . import service


@traced("sawc_write")
async def sawc_write(state: domains.dd.synth.state.SynthState) -> dict:
    return await service.sawc_write_run(state)
