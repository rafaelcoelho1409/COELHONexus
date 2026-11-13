"""LangGraph adapters for YCS Ask preparation and routing."""
from __future__ import annotations

import asyncio

import domains
from .... import graphs


async def prepare(state: graphs.ask.adaptive.states.AdaptiveAskState, llm, neo4j_graph = None) -> dict:
    """Contextualize the question and classify its retrieval route concurrently."""
    contextualized, classified = await asyncio.gather(
        domains.ycs.ask.rag.adaptive.nodes.contextualize.node.contextualize_question(state, llm),
        domains.ycs.ask.rag.adaptive.nodes.classify.node.classify_query(state, llm, neo4j_graph),
    )
    return {**contextualized, **classified}


def route_by_mode(state: graphs.ask.adaptive.states.AdaptiveAskState) -> str:
    """Route conversation, deep research, and standard retrieval requests."""
    if state.get("intent") == "conversation":
        return "direct_answer"
    if state.get("mode", "standard").lower() == "deep":
        return "plan_research"
    return "run_standard"


def route_after_direct(state: graphs.ask.adaptive.states.AdaptiveAskState) -> str:
    """Fall back to transcript retrieval when a direct answer failed."""
    answer = str(state.get("generation") or "")
    if answer and not answer.startswith("Error generating answer:"):
        return "end"
    return "run_standard"
