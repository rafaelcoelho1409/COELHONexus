"""Typed transient state for parallel BD source-specialist retrieval."""
from __future__ import annotations

from typing import Any, TypedDict


class RetrievalGraphState(TypedDict, total = False):
    query: str
    include_web: bool
    elasticsearch_result: dict[str, Any]
    qdrant_result: dict[str, Any]
    neo4j_result: dict[str, Any]
    search_mcp_result: dict[str, Any]
    evidence: list[dict[str, Any]]
    source_status: dict[str, str]
    source_queries: dict[str, list[str]]
