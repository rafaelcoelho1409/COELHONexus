"""plan_write graph node: persists the final chapter plan."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


@domains.dd.pipeline.observability.service.traced("plan_write")
async def plan_write(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.plan_write_run(state, runtime.context.blobs)
