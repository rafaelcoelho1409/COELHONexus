"""Thin LangGraph node for per-figure vision enrichment."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def enrich_visuals(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    context = runtime.context
    return await graphs.service.run_node(
        context.progress_callback,
        "enrich_visuals",
        lambda: service.enrich_visuals(
            graph_state,
            context.read_page,
            context.read_source_figure,
            context.write_artifact,
            context.artifact_exists,
            context.visual_extractor,
            context.progress_callback,
            context.cancel_check,
        ),
    )
