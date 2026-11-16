"""Thin LangGraph wrapper for selected-page materialization."""
from __future__ import annotations
import domains
from .... import graphs

from langgraph.runtime import Runtime


async def materialize_processed_book(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "materialize_processed_book",
        lambda: domains.bd.ingestion.ai.processed_book.service.materialize_processed_book(
            graph_state,
            runtime.context.read_page,
            runtime.context.write_artifact,
            runtime.context.artifact_exists,
        ),
    )
