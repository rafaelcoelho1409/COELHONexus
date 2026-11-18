"""Thin LangGraph node for Neo4j knowledge-graph indexing."""
from __future__ import annotations
from . import service
from .... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def index_book_graph(
    graph_state: graphs.rag.states.ProcessedBookIndexState,
    runtime: Runtime[graphs.rag.entities.ProcessedBookIndexDependencies],
) -> dict[str, Any]:
    context = runtime.context
    return await graphs.rag.service.run_index_node(
        context,
        "neo4j",
        lambda: service.index_book_graph(graph_state, context),
    )
