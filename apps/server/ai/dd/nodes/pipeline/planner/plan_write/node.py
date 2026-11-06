"""plan_write graph node: persists the final chapter plan."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


@traced("plan_write")
async def plan_write(
    state: graphs.pipeline.planner.state.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.context.PlannerContext],
) -> dict:
    return await service.plan_write_run(state, runtime.context.blobs)
