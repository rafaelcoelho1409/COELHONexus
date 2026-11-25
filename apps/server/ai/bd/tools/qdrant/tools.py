"""DeepAgents tool for BD Qdrant retrieval."""
from __future__ import annotations
from ..retrieval import entities, service

from langchain.tools import ToolRuntime, tool


@tool
async def retrieve_qdrant(query: str, runtime: ToolRuntime[entities.RetrievalSession]) -> str:
    """Search the current BD book index using dense and sparse semantic retrieval."""
    return await service.call_source_tool(
        runtime.context,
        query,
        source = "qdrant",
    )
