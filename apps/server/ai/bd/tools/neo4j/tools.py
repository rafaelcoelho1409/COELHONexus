"""DeepAgents tool for BD Neo4j retrieval."""
from __future__ import annotations
from ..retrieval import entities, service

from langchain.tools import ToolRuntime, tool


@tool
async def retrieve_neo4j(query: str, runtime: ToolRuntime[entities.RetrievalSession]) -> str:
    """Search the current BD book graph for relevant entities and relationships."""
    return await service.call_source_tool(
        runtime.context,
        query,
        source = "neo4j",
    )
