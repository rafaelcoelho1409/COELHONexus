"""LangGraph node wrapper for bounded query rewriting."""
from __future__ import annotations
from . import service
from ...... import graphs

from langgraph.runtime import Runtime


async def rewrite_queries(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Rewrite retrieval queries under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "rewrite_query",
        lambda: service.rewrite_queries(graph_state, runtime),
    )
