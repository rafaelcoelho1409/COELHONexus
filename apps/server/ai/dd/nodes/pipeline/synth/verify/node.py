"""Synth v2 graph node for static and optional sandbox verification."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


@domains.dd.pipeline.observability.service.traced_synth("verify")
async def verify_code(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.verify_code(state, runtime)
