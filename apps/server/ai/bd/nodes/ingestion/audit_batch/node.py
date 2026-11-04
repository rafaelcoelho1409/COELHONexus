"""Thin LangGraph node for full-book page auditing."""
from __future__ import annotations
from . import service
from .... import graphs


async def audit_pages(graph_state: graphs.ingestion.state.IngestionRunState, read_page, auditor, progress_callback = None) -> dict:
    return await service.audit_pages(graph_state, read_page, auditor, progress_callback)
