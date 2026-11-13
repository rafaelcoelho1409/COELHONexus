"""LangGraph adapter for corpus-free YCS Ask replies."""
from __future__ import annotations

import domains
from .... import graphs


async def direct_answer(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    llm,
    llm_fast = None,
) -> dict:
    """Answer conversational turns without transcript retrieval."""
    return await domains.ycs.ask.rag.adaptive.nodes.direct_answer.node.direct_answer(
        state,
        llm_fast or llm,
    )
