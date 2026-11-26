"""Thin LangGraph node for Neo4j knowledge-graph indexing."""
from __future__ import annotations
from . import service
from ...... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def index_book_graph(
    graph_state: graphs.ingestion.rag_indexing.states.ProcessedBookIndexState,
    runtime: Runtime[graphs.ingestion.rag_indexing.entities.ProcessedBookIndexDependencies],
) -> dict[str, Any]:
    context = runtime.context
    return await graphs.ingestion.rag_indexing.service.run_index_node(
        context,
        "neo4j",
        lambda: service.index_book_graph(graph_state, context),
    )
