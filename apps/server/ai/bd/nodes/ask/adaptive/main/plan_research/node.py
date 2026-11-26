"""LangGraph research-planning node and route selection."""
from __future__ import annotations
from . import service
from ...... import graphs

from langgraph.runtime import Runtime
from typing import Literal


async def plan_research(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Plan evidence research under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "plan_research",
        lambda: service.plan_research(graph_state, runtime),
    )


def route_after_plan(graph_state: graphs.ask.adaptive.states.AdaptiveAskState) -> Literal["conversation", "book"]:
    """Keep corpus-free conversation out of the retrieval and verification path."""
    return "conversation" if graph_state.get("intent") == "conversation" else "book"
