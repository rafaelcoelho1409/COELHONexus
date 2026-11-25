"""Source-specific DeepAgents tools backed by the shared FastMCP protocol."""
from __future__ import annotations
from . import entities, service

from langchain.tools import ToolRuntime, tool


@tool
async def rag_retrieve_elasticsearch(query: str, runtime: ToolRuntime[entities.RetrievalSession]) -> str:
    """Search the current BD book index using exact terms, phrases, and BM25 relevance."""
    return await service.call_source_tool(runtime.context, query)


@tool
async def rag_retrieve_qdrant(query: str, runtime: ToolRuntime[entities.RetrievalSession]) -> str:
    """Search the current BD book index using dense and sparse semantic retrieval."""
    return await service.call_source_tool(runtime.context, query)


@tool
async def rag_retrieve_neo4j(query: str, runtime: ToolRuntime[entities.RetrievalSession]) -> str:
    """Search the current BD book graph for relevant entities and relationships."""
    return await service.call_source_tool(runtime.context, query)


@tool
async def rag_retrieve_search_mcp(query: str, runtime: ToolRuntime[entities.RetrievalSession]) -> str:
    """Search the Settings-selected external Search MCP for current web sources."""
    return await service.call_source_tool(runtime.context, query)
