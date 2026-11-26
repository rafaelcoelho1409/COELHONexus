"""Register BD Agentic Ingestion and RAG resource templates."""
from __future__ import annotations
from . import ingestion

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    ingestion.book_processing.registry.register_resources(mcp)
    ingestion.rag_indexing.registry.register_resources(mcp)
