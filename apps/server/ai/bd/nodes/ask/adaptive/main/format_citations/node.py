"""LangGraph node wrapper for citation formatting."""
from __future__ import annotations
from . import domain
from ...... import graphs


async def format_citations(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
) -> dict:
    """Format citations under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "format_citations",
        lambda: domain.format_citations(graph_state),
    )
