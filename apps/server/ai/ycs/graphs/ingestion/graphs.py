"""Compile the standalone YCS video ingestion LangGraph."""
from __future__ import annotations
from . import entities, states
from ... import nodes

from typing import Any

from langgraph.graph import END, START, StateGraph


def ingestion_graph(checkpointer: Any | None = None):
    """Compile four data-processing nodes with independent store branches."""
    workflow = StateGraph(
        states.IngestionChunkState,
        context_schema = entities.IngestionContext,
    )
    workflow.add_node("playwright", nodes.ingestion.playwright.node.fetch_transcripts)
    workflow.add_node("elasticsearch", nodes.rag.elasticsearch_index.node.index_documents)
    workflow.add_node("qdrant", nodes.rag.qdrant_index.node.index_vectors)
    workflow.add_node("neo4j", nodes.rag.neo4j_index.node.extract_graph)
    workflow.add_edge(START, "playwright")
    workflow.add_edge("playwright", "elasticsearch")
    workflow.add_conditional_edges(
        "elasticsearch",
        nodes.rag.elasticsearch_index.node.dispatch_to_stores,
        {"qdrant": "qdrant", "neo4j": "neo4j", "done": END},
    )
    workflow.add_edge("qdrant", END)
    workflow.add_edge("neo4j", END)
    return workflow.compile(checkpointer = checkpointer, name = "ycs_ingestion")


__all__ = ["ingestion_graph"]
