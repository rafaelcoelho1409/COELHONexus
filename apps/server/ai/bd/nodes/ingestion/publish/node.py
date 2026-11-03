"""Thin LangGraph node for report and manifest publication."""
from __future__ import annotations
from . import service
from .... import graphs


async def publish(graph_state: graphs.ingestion.state.IngestionRunState, write_artifact) -> dict:
    return await service.publish(graph_state, write_artifact)
