"""LangGraph adapter for the YCS standard-retrieval subgraph."""
from __future__ import annotations

from . import service
from .... import graphs

from langchain_core.runnables import RunnableConfig


async def run_standard_pipeline(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    retriever,
    grader,
    llm,
    checkpointer = None,
    config: RunnableConfig | None = None,
) -> dict:
    """Build and invoke the channel-scoped standard-retrieval graph."""
    return await service.run_standard_pipeline(
        state,
        retriever,
        grader,
        llm,
        checkpointer,
        config,
    )
