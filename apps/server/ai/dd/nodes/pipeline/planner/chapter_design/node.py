"""Planner v2 chapter-design graph node."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


@traced("chapter_design")
async def chapter_design(state: graphs.pipeline.planner.states.PlannerState, runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext]) -> dict:
    return await service.chapter_design_run(state, runtime)
