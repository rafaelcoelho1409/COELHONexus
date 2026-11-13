"""LangGraph node wrapper for YCS multi-hop research planning."""
from __future__ import annotations

import logging

import domains

from . import service
from .... import graphs


logger = logging.getLogger(__name__)


async def plan_research(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    llm,
) -> dict:
    """Run the DeepAgent planner, falling back to the legacy planner on failure."""
    try:
        return await service.plan_research(state, llm)
    except Exception as error:
        logger.warning(
            "[ycs:adaptive] DeepAgents planner unavailable; using legacy planner: %s: %s",
            type(error).__name__,
            error,
        )
        return await domains.ycs.ask.rag.adaptive.nodes.plan.node.plan_research(state, llm)
