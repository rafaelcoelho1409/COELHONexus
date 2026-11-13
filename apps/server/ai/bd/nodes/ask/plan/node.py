"""LangGraph node wrappers for adaptive BD Ask planning."""
from __future__ import annotations
from . import service
from .... import graphs

from langchain.runtime import Runtime


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


async def rewrite_queries(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Rewrite search queries under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "rewrite_query",
        lambda: service.rewrite_queries(graph_state, runtime),
    )
