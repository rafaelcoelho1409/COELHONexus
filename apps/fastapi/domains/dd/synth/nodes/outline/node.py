"""outline_sdp — LangGraph node; all orchestration in service.outline_sdp_run."""
from __future__ import annotations
import domains
from domains.dd.synth.runtime.observability.service import traced

from . import service


@traced("outline_sdp")
async def outline_sdp(state: domains.dd.synth.state.SynthState) -> dict:
    return await service.outline_sdp_run(state)
