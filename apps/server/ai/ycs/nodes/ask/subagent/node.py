"""LangGraph adapter for bounded YCS Ask subagent fan-out."""
from __future__ import annotations

from . import service
from .... import graphs


async def run_subagents(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    retriever,
    grader,
    llm,
    checkpointer = None,
) -> dict:
    """Run the planned subquestions with the graph's shared concurrency bound."""
    return await service.run_subagents(state, retriever, grader, llm, checkpointer)
