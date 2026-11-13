"""LangGraph node wrappers for answer generation and citation formatting."""
from __future__ import annotations
from . import domain, service
from .... import graphs

from langgraph.runtime import Runtime


async def generate_answer(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Run answer generation under graph progress reporting."""
    node = "generate" if graph_state.get("mode") != "deep" else "synthesize"
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        node,
        lambda: service.generate_answer(graph_state, runtime),
    )


async def format_citations(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
) -> dict:
    """Format citations under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "format_citations",
        lambda: domain.format_citations(graph_state),
    )
