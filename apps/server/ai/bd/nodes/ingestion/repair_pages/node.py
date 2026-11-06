"""Thin LangGraph node for bounded page repair and output persistence."""
from __future__ import annotations
from . import service
from .... import graphs


async def repair_pages(
    graph_state: graphs.ingestion.state.IngestionRunState, 
    read_page, 
    write_artifact, 
    specialists, 
    progress_callback = None) -> dict:
    return await service.repair_pages(
        graph_state, 
        read_page, 
        write_artifact, 
        specialists, 
        progress_callback)
