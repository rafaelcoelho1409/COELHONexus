"""Register tools owned by the BD domain."""

from __future__ import annotations

from . import ask, distill, ingestion, query

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Register all BD-owned tools."""
    ask.adaptive.registry.register_tools(mcp)
    distill.book_distillation.registry.register_tools(mcp)
    ingestion.book_processing.registry.register_tools(mcp)
    ingestion.rag_indexing.registry.register_tools(mcp)
    query.registry.register_tools(mcp)
