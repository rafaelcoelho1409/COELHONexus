"""Register BD Ask, Distill, Ingestion, and Query resources."""

from __future__ import annotations
from . import ask, distill, ingestion, query

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    ask.adaptive.registry.register_resources(mcp)
    distill.book_distillation.registry.register_resources(mcp)
    ingestion.book_processing.registry.register_resources(mcp)
    ingestion.rag_indexing.registry.register_resources(mcp)
    query.registry.register_resources(mcp)
