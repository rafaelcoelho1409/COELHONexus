"""Thin LangGraph node for full-book page auditing."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def audit_pages(
    graph_state: graphs.ingestion.states.IngestionRunState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict:
    return await graphs.service.run_node(
        runtime.context.progress_callback,
        "audit_batch",
        lambda: service.audit_pages(
            graph_state,
            runtime.context.read_page,
            runtime.context.auditor,
            runtime.context.progress_callback,
            runtime.context.finding_verifier,
        ),
    )
