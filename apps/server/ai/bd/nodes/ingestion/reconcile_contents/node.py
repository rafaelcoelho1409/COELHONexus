"""Thin LangGraph wrapper for canonical contents construction."""
from __future__ import annotations
import domains
from .... import graphs

from langgraph.runtime import Runtime


async def reconcile_contents(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    async def report_page_progress(completed: int, total: int, active: int) -> None:
        await graphs.service.report_node_page_progress(
            runtime.context.progress_callback,
            "reconcile_contents",
            completed,
            total,
            active,
        )

    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "reconcile_contents",
        lambda: domains.bd.ingestion.ai.processed_book.service.reconcile_contents(
            graph_state,
            runtime.context.read_page,
            runtime.context.write_artifact,
            runtime.context.artifact_exists,
            runtime.context.cancel_check,
            report_page_progress,
        ),
    )
