"""Compile the parallel book retrieval and evidence fusion workflow."""
from __future__ import annotations
from ... import entities, states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def book_retrieval_graph():
    """Compile retrieval preparation, parallel source retrieval, and evidence fusion."""
    workflow = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
    workflow.add_node(
        "prepare_retrieval",
        nodes.distill.subgraphs.book_retrieval.prepare.node.prepare_retrieval,
    )
    workflow.add_node(
        "elasticsearch",
        nodes.distill.subgraphs.book_retrieval.elasticsearch.node.elasticsearch_retrieval,
    )
    workflow.add_node(
        "qdrant",
        nodes.distill.subgraphs.book_retrieval.qdrant.node.qdrant_retrieval,
    )
    workflow.add_node(
        "neo4j",
        nodes.distill.subgraphs.book_retrieval.neo4j.node.neo4j_retrieval,
    )
    workflow.add_node(
        "web_search",
        nodes.distill.subgraphs.book_retrieval.web_search.node.web_search_retrieval,
    )
    workflow.add_node(
        "fuse_evidence",
        nodes.distill.subgraphs.book_retrieval.fuse.node.fuse_evidence,
    )

    workflow.add_edge(START, "prepare_retrieval")
    workflow.add_edge("prepare_retrieval", "elasticsearch")
    workflow.add_edge("prepare_retrieval", "qdrant")
    workflow.add_edge("prepare_retrieval", "neo4j")
    workflow.add_edge("prepare_retrieval", "web_search")
    workflow.add_edge(
        ["elasticsearch", "qdrant", "neo4j", "web_search"],
        "fuse_evidence",
    )
    workflow.add_edge("fuse_evidence", END)
    return workflow.compile()
