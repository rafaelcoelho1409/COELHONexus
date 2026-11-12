"""Synth v2 graph node for deterministic chapter code-ledger preparation."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import service
from ..... import graphs

import langgraph.runtime


@domains.dd.pipeline.observability.service.traced_synth("ledger_prepare")
async def ledger_prepare(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.ledger_prepare(state, runtime)
