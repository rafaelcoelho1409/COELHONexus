"""Thin LangGraph node for report and manifest publication."""
from __future__ import annotations
from . import service
from .... import graphs

import langgraph.runtime


async def publish(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: langgraph.runtime.Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "publish",
        lambda: service.publish(
            graph_state,
            runtime.context.write_artifact,
            runtime.context.read_page,
        ),
    )
