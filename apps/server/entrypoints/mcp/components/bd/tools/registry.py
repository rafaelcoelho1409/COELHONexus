"""Register BD Distill retrieval, Ingestion, and RAG indexing tools."""
from __future__ import annotations
from . import distill, ingestion

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Register all BD-owned tools."""
    distill.book_distillation.registry.register_tools(mcp)
    ingestion.book_processing.registry.register_tools(mcp)
    ingestion.rag_indexing.registry.register_tools(mcp)
