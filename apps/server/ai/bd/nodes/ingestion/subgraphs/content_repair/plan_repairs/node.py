"""Thin LangGraph node for deterministic specialist routing."""
from __future__ import annotations
import domains
from ...... import graphs

from langgraph.runtime import Runtime


async def plan_repairs(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    if "plan_repairs" in graph_state.get("replay_skip_nodes", []):
        return {}
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "plan_repairs",
        lambda: domains.bd.ingestion.ai.repair_planning.domain.plan_repairs(graph_state),
    )
