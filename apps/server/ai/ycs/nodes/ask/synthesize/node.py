"""LangGraph adapter for YCS Ask evidence synthesis."""
from __future__ import annotations

import domains
from .... import graphs


async def synthesize(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    llm,
) -> dict:
    """Synthesize the bounded subagent results into the final response."""
    return await domains.ycs.ask.rag.adaptive.nodes.synthesize.node.synthesize(state, llm)
