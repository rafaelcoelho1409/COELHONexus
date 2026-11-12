"""LangGraph node adapter for concurrent ingestion page normalization."""
from __future__ import annotations
from . import service
from .... import graphs

import typing

import langgraph.runtime


async def normalize_pages(
    state: graphs.ingestion.states.IngestionState,
    runtime: langgraph.runtime.Runtime[graphs.ingestion.entities.IngestionDependencies],
) -> dict[str, typing.Any]:
    """Run the normalization service as the ingestion graph's repair node, under progress reporting."""
    total = len(state.get("pages", []))
    return await graphs.service.run_node(
        runtime.context.progress,
        "normalize_pages",
        lambda: service.normalize_pages(
            state,
            runtime.context.read_page,
            runtime.context.artifact_exists,
            runtime.context.read_artifact,
            runtime.context.write_artifact,
            runtime.context.repairer,
            runtime.context.progress,
            lambda: graphs.ingestion.service.page_done(
                runtime.context, 
                "normalize_pages", 
                total),
        ),
    )
