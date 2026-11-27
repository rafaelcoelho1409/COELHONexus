"""Register BD Ask, Distill, Ingestion, and Query prompts."""

from __future__ import annotations
from . import ask, distill, ingestion, query

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    ask.adaptive.registry.register_prompts(mcp)
    distill.book_distillation.registry.register_prompts(mcp)
    ingestion.book_processing.registry.register_prompts(mcp)
    ingestion.rag_indexing.registry.register_prompts(mcp)
    query.registry.register_prompts(mcp)
