"""Standard-retrieval subgraph orchestration for YCS Ask."""
from __future__ import annotations

import domains
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
    """Invoke the standard pipeline scoped to the request's channels."""
    standard_graph = domains.ycs.ask.rag.standard.graph.build_youtube_rag_graph(
        retriever = retriever,
        grader = grader,
        llm = llm,
        checkpointer = checkpointer,
        channel_ids = state.get("channel_ids") or None,
    )
    return await domains.ycs.ask.rag.adaptive.nodes.run_standard.node.run_standard_pipeline(
        state,
        standard_graph,
        config,
    )
