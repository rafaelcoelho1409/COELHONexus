"""Shared topology for BD book retrieval across Ask and Distill."""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph


BOOK_RAG_SOURCE_NAMES = ("elasticsearch", "qdrant", "neo4j", "web_search")


def compile_book_rag_retrieval_graph(
    *,
    state_schema: Any,
    context_schema: Any,
    prepare_node: Any,
    elasticsearch_node: Any,
    qdrant_node: Any,
    neo4j_node: Any,
    web_search_node: Any,
    fuse_node: Any,
) -> Any:
    """Compile the canonical prepare → four parallel sources → fuse workflow.

    Search MCP is an optional source in either consumer. Keeping the graph builder shared
    prevents Ask and Distill from drifting in their indexed retrieval fan-out and join.
    """
    workflow = StateGraph(state_schema, context_schema = context_schema)
    workflow.add_node("prepare_retrieval", prepare_node)
    workflow.add_node("elasticsearch", elasticsearch_node)
    workflow.add_node("qdrant", qdrant_node)
    workflow.add_node("neo4j", neo4j_node)
    workflow.add_node("web_search", web_search_node)
    workflow.add_node("fuse_evidence", fuse_node)

    workflow.add_edge(START, "prepare_retrieval")
    for source in BOOK_RAG_SOURCE_NAMES:
        workflow.add_edge("prepare_retrieval", source)
    workflow.add_edge(list(BOOK_RAG_SOURCE_NAMES), "fuse_evidence")
    workflow.add_edge("fuse_evidence", END)
    return workflow.compile()
