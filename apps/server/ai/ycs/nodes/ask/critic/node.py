"""LangGraph node wrapper for the YCS evidence critic."""
from __future__ import annotations
from . import service
from .... import graphs


async def critic(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    llm,
) -> dict:
    """Run evidence verification as a LangGraph node."""
    return await service.critic(state, llm)
