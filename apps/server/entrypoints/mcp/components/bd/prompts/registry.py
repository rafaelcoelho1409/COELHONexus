"""Register focused BD Distill, Ingestion, and RAG prompts."""
from __future__ import annotations
from . import distill, ingestion

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    distill.book_distillation.registry.register_prompts(mcp)
    ingestion.book_processing.registry.register_prompts(mcp)
    ingestion.rag_indexing.registry.register_prompts(mcp)
