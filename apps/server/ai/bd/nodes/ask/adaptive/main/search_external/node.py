"""LangGraph adapter for optional Search MCP evidence retrieval."""
from __future__ import annotations
from . import service
from ...... import graphs

from typing import Literal
from langgraph.runtime import Runtime


async def search_external_evidence(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Call the selected Search MCP only when the planner identifies a need."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "search_external",
        lambda: service.search_external_evidence(graph_state, runtime),
    )


def route_after_book_retrieval(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
) -> Literal["search", "skip"]:
    """Only enter the remote Search MCP lane when the plan explicitly requests it."""
    return "search" if graph_state.get("use_external_search") else "skip"
