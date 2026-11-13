"""LangGraph node wrapper for bounded parallel BD Ask retrieval."""
from __future__ import annotations
from . import service
from .... import graphs

from langchain.runtime import Runtime


async def retrieve_evidence(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Retrieve book evidence under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "run_subagent",
        lambda: service.retrieve_evidence(graph_state, runtime),
    )
