"""Thin LangGraph node for metadata normalization."""
from __future__ import annotations
from . import service
from .... import graphs


async def normalize_metadata(graph_state: graphs.ingestion.state.IngestionRunState, read_page, write_artifact, normalizer) -> dict:
    return await service.normalize_metadata(graph_state, read_page, write_artifact, normalizer)
