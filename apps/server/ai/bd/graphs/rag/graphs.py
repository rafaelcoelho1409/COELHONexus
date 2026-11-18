"""Compile the post-publication retrieval-index graph for a processed book."""
from __future__ import annotations
import domains
from . import entities, states
from ... import nodes

from typing import Any

from langgraph.graph import END, START, StateGraph


def retrieval_index_graph(checkpointer: Any | None = None):
    """Compile the full-text, vector, and knowledge-graph indexing workflow."""
    workflow = StateGraph(
        states.ProcessedBookIndexState,
        context_schema = entities.ProcessedBookIndexDependencies,
    )
    workflow.add_node("pin_sources", nodes.rag.pin_sources.node.pin_sources)
    workflow.add_node("elasticsearch", nodes.rag.elasticsearch_index.node.index_pages)
    workflow.add_node("qdrant", nodes.rag.qdrant_index.node.index_pages)
    workflow.add_node("neo4j", nodes.rag.neo4j_index.node.index_book_graph)
    workflow.add_node(
        "publish_retrieval_index",
        nodes.rag.publish_retrieval_index.node.publish_retrieval_index,
    )
    workflow.add_edge(START, "pin_sources")
    workflow.add_edge("pin_sources", "elasticsearch")
    workflow.add_conditional_edges(
        "elasticsearch",
        domains.bd.ingestion.ai.retrieval_index.domain.dispatch_to_stores,
        ["qdrant", "neo4j"],
    )
    workflow.add_edge(["qdrant", "neo4j"], "publish_retrieval_index")
    workflow.add_conditional_edges(
        "qdrant",
        lambda state: domains.bd.ingestion.ai.retrieval_index.domain.publish_after_replayed_store(
            state,
            "qdrant",
        ),
        ["publish_retrieval_index"],
    )
    workflow.add_conditional_edges(
        "neo4j",
        lambda state: domains.bd.ingestion.ai.retrieval_index.domain.publish_after_replayed_store(
            state,
            "neo4j",
        ),
        ["publish_retrieval_index"],
    )
    workflow.add_edge("publish_retrieval_index", END)
    return workflow.compile(checkpointer = checkpointer, name = "bd_ingestion_retrieval_index")
