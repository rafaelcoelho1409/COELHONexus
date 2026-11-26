"""Thin LangGraph node for report and manifest publication."""
from __future__ import annotations
from . import service
from ....... import graphs

from langgraph.runtime import Runtime


async def publish(
    graph_state: graphs.ingestion.book_processing.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.book_processing.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "publish",
        lambda: service.publish(
            graph_state,
            runtime.context.write_artifact,
            runtime.context.read_page,
            runtime.context.progress_callback,
        ),
    )
