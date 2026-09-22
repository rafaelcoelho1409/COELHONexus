"""digest_construct — LangGraph node."""
from __future__ import annotations
import domains
from domains.dd.synth.runtime.observability.service import traced
from . import service


@traced("digest_construct")
async def digest_construct(state: domains.dd.synth.state.SynthState) -> dict:
    return await service.digest_construct_run(state)
