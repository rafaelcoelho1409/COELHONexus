"""Thin LangGraph node for deterministic specialist routing."""
from __future__ import annotations
from . import domain
from .... import graphs

from langgraph.runtime import Runtime


async def plan_repairs(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "plan_repairs",
        lambda: domain.plan_repairs(graph_state),
    )
