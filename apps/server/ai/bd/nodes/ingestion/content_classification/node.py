"""Thin LangGraph wrapper for the full-book content selection pass."""
from __future__ import annotations

from langgraph.runtime import Runtime

from .... import graphs
from . import service


async def classify_content(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    context = runtime.context
    return await graphs.service.run_node(
        context.progress_callback,
        "classify_content",
        lambda: service.classify_content(
            graph_state,
            context.read_page,
            context.write_artifact,
            context.artifact_exists,
            context.content_classifier,
            context.progress_callback,
        ),
    )
