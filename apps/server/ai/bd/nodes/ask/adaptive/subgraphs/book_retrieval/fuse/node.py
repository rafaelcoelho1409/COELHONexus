"""LangGraph wrapper for Ask retrieval evidence fusion."""
from __future__ import annotations

from . import service
from ....... import graphs


async def fuse_evidence(state: dict) -> dict:
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "fuse_evidence",
        lambda: service.fuse_evidence(state),
    )
