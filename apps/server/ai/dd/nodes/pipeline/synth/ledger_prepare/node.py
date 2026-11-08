"""Synth v2 graph node for deterministic chapter code-ledger preparation."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("ledger_prepare")
async def ledger_prepare(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.ledger_prepare(state, runtime)
