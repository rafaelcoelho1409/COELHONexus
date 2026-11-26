"""Register focused BD Ingestion and RAG diagnosis prompts."""
from __future__ import annotations

from fastmcp import FastMCP

from . import ingestion, rag_indexing


def register_prompts(mcp: FastMCP) -> None:
    mcp.prompt(
        name = "bd_review_ingestion_run",
        title = "Review BD Ingestion Run",
        description = "Review run progress and aggregate quality evidence without mutating the run.",
        tags = {"bd", "ingestion", "review"},
    )(ingestion.review_ingestion_run)
    mcp.prompt(
        name = "bd_diagnose_rag_index_run",
        title = "Diagnose BD RAG Index Run",
        description = "Diagnose indexing stages and recommend a safe replay point without executing it.",
        tags = {"bd", "rag", "diagnostics"},
    )(rag_indexing.diagnose_index_run)
