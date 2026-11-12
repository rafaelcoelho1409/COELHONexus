"""Planner v2 page-card graph node."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import service
from ..... import graphs

import langgraph.runtime


@domains.dd.pipeline.observability.service.traced("page_cards")
async def page_cards(state: graphs.pipeline.planner.states.PlannerState, runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext]) -> dict:
    return await service.page_cards_run(state, runtime)
