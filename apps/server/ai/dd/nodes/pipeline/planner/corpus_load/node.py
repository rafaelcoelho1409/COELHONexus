"""corpus_load graph node: a deterministic step, no model."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import service
from ..... import graphs

import langgraph.runtime


@domains.dd.pipeline.observability.service.traced("corpus_load")
async def corpus_load(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.corpus_load_run(state, runtime.context.blobs)
