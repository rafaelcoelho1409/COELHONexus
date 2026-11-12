"""LangGraph node adapter for manifest-last publication."""
from __future__ import annotations
from . import service
from .... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def publish(
    state: graphs.ingestion.states.IngestionState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict[str, Any]:
    """Run the publication service as the ingestion graph's last node, under progress reporting."""
    return await graphs.service.run_node(
        runtime.context.progress,
        "publish",
        lambda: service.publish(state, runtime.context.write_artifact),
    )
