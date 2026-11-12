"""Thin LangGraph node for bounded page repair and output persistence."""
from __future__ import annotations
from . import service
from .... import graphs

import langgraph.runtime


async def repair_pages(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: langgraph.runtime.Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "repair_pages",
        lambda: service.repair_pages(
            graph_state,
            runtime.context.read_page,
            runtime.context.write_artifact,
            runtime.context.specialists,
            runtime.context.progress_callback,
        ),
    )
