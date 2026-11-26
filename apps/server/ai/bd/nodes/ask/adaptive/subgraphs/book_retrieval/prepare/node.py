"""LangGraph wrapper for Ask retrieval preparation."""
from __future__ import annotations
from . import service
from ....... import graphs


async def prepare_retrieval(state: dict) -> dict:
    """Normalize query state and report the preparation node lifecycle."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "prepare_retrieval",
        lambda: service.prepare_retrieval(state),
    )
