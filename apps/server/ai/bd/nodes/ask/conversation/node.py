"""LangGraph adapter for corpus-free BD Ask replies."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def direct_answer(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Answer a conversational turn without searching or making book claims."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "direct_answer",
        lambda: service.direct_answer(graph_state, runtime),
    )
