"""Register focused BD Ingestion and RAG diagnosis prompts."""
from __future__ import annotations
from . import ingestion

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    ingestion.book_processing.registry.register_prompts(mcp)
    ingestion.rag_indexing.registry.register_prompts(mcp)
