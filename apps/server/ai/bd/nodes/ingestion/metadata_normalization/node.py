"""Thin LangGraph node for metadata normalization."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def normalize_metadata(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "normalize_metadata",
        lambda: service.normalize_metadata(
            graph_state,
            runtime.context.read_page,
            runtime.context.write_artifact,
            runtime.context.metadata_normalizer,
        ),
    )
