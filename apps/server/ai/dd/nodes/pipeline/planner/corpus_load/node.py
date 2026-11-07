"""corpus_load graph node: a deterministic step, no model."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


@traced("corpus_load")
async def corpus_load(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.corpus_load_run(state, runtime.context.blobs)
