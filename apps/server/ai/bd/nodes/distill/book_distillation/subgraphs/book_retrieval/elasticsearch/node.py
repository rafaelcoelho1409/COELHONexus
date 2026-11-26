"""Elasticsearch retrieval node for the Distill pipeline."""
from __future__ import annotations
from . import service
from ....... import graphs

from langgraph.runtime import Runtime


async def elasticsearch_retrieval(
    state: graphs.distill.book_distillation.states.DistillRunState,
    runtime: Runtime[graphs.distill.book_distillation.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        requested_sources = state.get("retrieval_sources_to_run")
        if isinstance(requested_sources, list) and "elasticsearch" not in requested_sources:
            return {}
        await graphs.service.report_node_activity(
            dependencies.progress_callback,
            "elasticsearch",
            "Querying Elasticsearch through its MCP retrieval tool",
        )
        return await service.retrieve(state, dependencies)

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "elasticsearch",
        execute,
        controls=dependencies.controls,
    )
