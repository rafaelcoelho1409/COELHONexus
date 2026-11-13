"""LangGraph node wrapper for evidence-grounded BD Ask verification."""
from __future__ import annotations
from . import service
from .... import graphs

from langchain.runtime import Runtime


async def verify_answer(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Run answer verification under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "critic",
        lambda: service.verify_answer(graph_state, runtime),
    )
