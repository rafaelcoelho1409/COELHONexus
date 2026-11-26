"""Register BD Agentic Ingestion and RAG resource templates."""
from __future__ import annotations

from fastmcp import FastMCP

from . import ingestion, rag_indexing


def register_resources(mcp: FastMCP, *, trusted_in_process: bool = False) -> None:
    ingestion_handlers = ingestion.IngestionResources(trusted_in_process = trusted_in_process)
    rag_handlers = rag_indexing.RetrievalIndexResources(trusted_in_process = trusted_in_process)

    mcp.resource(
        "bd://books/{book_id}/ingestion/runs/{agentic_run_id}",
        name = "bd_ingestion_run",
        title = "BD Agentic Ingestion Run",
        description = "Compact run status and graph progress for one Agentic Ingestion run.",
        mime_type = "application/json",
        tags = {"bd", "ingestion", "run"},
    )(ingestion_handlers.run)
    mcp.resource(
        "bd://books/{book_id}/ingestion/runs/{agentic_run_id}/quality",
        name = "bd_ingestion_quality_summary",
        title = "BD Ingestion Quality Summary",
        description = "Aggregate quality counts for one completed or in-progress Ingestion run.",
        mime_type = "application/json",
        tags = {"bd", "ingestion", "quality"},
    )(ingestion_handlers.quality)
    mcp.resource(
        "bd://books/{book_id}/rag/index-runs/{agentic_run_id}",
        name = "bd_rag_index_run",
        title = "BD Agentic RAG Index Run",
        description = "Progress and backend counts for one processed-book indexing run.",
        mime_type = "application/json",
        tags = {"bd", "rag", "index", "run"},
    )(rag_handlers.run)
    mcp.resource(
        "bd://books/{book_id}/rag/current-index",
        name = "bd_rag_current_index",
        title = "BD Current Retrieval Index",
        description = "Summary of the validated index currently used by BD retrieval.",
        mime_type = "application/json",
        tags = {"bd", "rag", "index"},
    )(rag_handlers.current)
