"""Thin LangGraph node for deterministic specialist routing."""
from __future__ import annotations
import domains
from ....... import graphs

from langgraph.runtime import Runtime


async def plan_repairs(
    graph_state: graphs.ingestion.book_processing.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.book_processing.entities.IngestionDependencies],
) -> dict:
    if "plan_repairs" in graph_state.get("replay_skip_nodes", []):
        return {}

    async def execute() -> dict:
        work_items = []
        for completed, total, page_work_items in (
            domains.bd.ingestion.ai.repair_planning.domain.plan_repairs_by_page(graph_state)
        ):
            work_items.extend(page_work_items)
            await graphs.service.report_node_page_progress(
                runtime.context.progress_callback,
                "plan_repairs",
                completed,
                total,
                0,
            )
        return {"work_items": work_items}

    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "plan_repairs",
        execute,
    )
