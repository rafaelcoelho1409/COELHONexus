"""DeepAgents tool for BD Neo4j retrieval."""
from __future__ import annotations
import domains
from ..retrieval import entities, service

from langchain.tools import ToolRuntime, tool


@tool
async def retrieve_neo4j(
    query: str,
    runtime: ToolRuntime[entities.RetrievalSession],
    intent: domains.bd.rag.source.retrieval.schemas.Neo4jQueryIntent | None = None,
) -> str:
    """Search the current BD graph with schema-guided entity and relation hints.

    Args:
        query: Natural-language retrieval request.
        intent: Optional bounded hints for entity names/types and relation terms/direction.
    """
    return await service.call_source_tool(
        runtime.context,
        query,
        source = "neo4j",
        neo4j_intent = intent,
    )
