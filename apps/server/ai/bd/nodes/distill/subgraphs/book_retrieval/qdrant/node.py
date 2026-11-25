"""Qdrant retrieval node for the Distill pipeline."""
from __future__ import annotations

from . import service
from ...... import graphs

from langgraph.runtime import Runtime


async def qdrant_retrieval(
    state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        requested_sources = state.get("retrieval_sources_to_run")
        if isinstance(requested_sources, list) and service.SOURCE_NAME not in requested_sources:
            return {}
        await graphs.service.report_node_activity(
            dependencies.progress_callback,
            service.SOURCE_NAME,
            "Querying Qdrant through its MCP retrieval tool",
        )
        return await service.retrieve(state, dependencies)

    return await graphs.service.run_node(
        dependencies.progress_callback,
        service.SOURCE_NAME,
        execute,
        controls=dependencies.controls,
    )
