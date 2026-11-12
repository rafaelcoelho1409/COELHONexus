"""LangGraph node adapter for concurrent ingestion page audits."""
from __future__ import annotations
from . import service
from .... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def audit_pages(
    state: graphs.ingestion.states.IngestionState,
    runtime: Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict[str, Any]:
    """Run the batch audit service as the ingestion graph's audit node, under progress reporting."""
    total = len(state.get("pages", []))
    return await graphs.service.run_node(
        runtime.context.progress,
        "audit_pages",
        lambda: service.audit_pages(
            state,
            runtime.context.read_page,
            runtime.context.artifact_exists,
            runtime.context.read_artifact,
            runtime.context.write_artifact,
            runtime.context.auditor,
            runtime.context.progress,
            lambda: graphs.ingestion.service.page_done(
                runtime.context, 
                "audit_pages", 
                total),
        ),
    )
