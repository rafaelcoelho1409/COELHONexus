"""DeepAgents tool for BD Elasticsearch retrieval."""
from __future__ import annotations
from ..retrieval import entities, service

from langchain.tools import ToolRuntime, tool


@tool
async def retrieve_elasticsearch(query: str, runtime: ToolRuntime[entities.RetrievalSession]) -> str:
    """Search the current BD book index using exact terms, phrases, and BM25 relevance."""
    return await service.call_source_tool(
        runtime.context,
        query,
        source = "elasticsearch",
    )
