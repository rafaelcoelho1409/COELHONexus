"""Thin LangGraph node for bounded page repair and output persistence."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def repair_pages(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    if "repair_pages" in graph_state.get("replay_skip_nodes", []):
        return {}
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "repair_pages",
        lambda: service.repair_pages(
            graph_state,
            runtime.context.read_page,
            runtime.context.write_artifact,
            runtime.context.specialists,
            runtime.context.progress_callback,
            patch_verifier = runtime.context.patch_verifier,
            read_source_page_image = runtime.context.read_source_page_image,
        ),
    )
