"""Thin LangGraph wrapper for table-of-contents reconciliation."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def reconcile_contents(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "reconcile_contents",
        lambda: service.reconcile_contents(
            graph_state,
            runtime.context.read_page,
            runtime.context.write_artifact,
            runtime.context.artifact_exists,
            runtime.context.contents_reconciler,
        ),
    )
