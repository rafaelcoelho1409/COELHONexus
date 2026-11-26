"""Register BD Ingestion, RAG indexing, and Search tools."""
from __future__ import annotations
from . import ingestion, search

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Register all BD-owned tools."""
    search.registry.register_tools(mcp)
    ingestion.book_processing.registry.register_tools(mcp)
    ingestion.rag_indexing.registry.register_tools(mcp)
