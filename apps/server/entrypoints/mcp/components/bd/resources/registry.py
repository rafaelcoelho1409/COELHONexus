"""Register BD Distill, Agentic Ingestion, and RAG resources."""
from __future__ import annotations
from . import distill, ingestion

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    distill.book_distillation.registry.register_resources(mcp)
    ingestion.book_processing.registry.register_resources(mcp)
    ingestion.rag_indexing.registry.register_resources(mcp)
