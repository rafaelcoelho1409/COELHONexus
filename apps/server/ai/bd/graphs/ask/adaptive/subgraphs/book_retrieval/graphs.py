"""Compile Ask's parallel indexed-book retrieval workflow."""
from __future__ import annotations
from ... import entities, states
from ...... import nodes

from langgraph.graph import END, START, StateGraph


def book_retrieval_graph():
    """Run the three book indexes in parallel, then fuse their evidence."""
    workflow = StateGraph(
        states.BookRetrievalState,
        context_schema = entities.AskContext,
    )
    workflow.add_node(
        "prepare_retrieval",
        nodes.ask.adaptive.subgraphs.book_retrieval.prepare.node.prepare_retrieval,
    )
    workflow.add_node(
        "elasticsearch",
        nodes.ask.adaptive.subgraphs.book_retrieval.elasticsearch.node.retrieve,
    )
    workflow.add_node(
        "qdrant",
        nodes.ask.adaptive.subgraphs.book_retrieval.qdrant.node.retrieve,
    )
    workflow.add_node(
        "neo4j",
        nodes.ask.adaptive.subgraphs.book_retrieval.neo4j.node.retrieve,
    )
    workflow.add_node(
        "fuse_evidence",
        nodes.ask.adaptive.subgraphs.book_retrieval.fuse.node.fuse_evidence,
    )

    workflow.add_edge(START, "prepare_retrieval")
    workflow.add_edge("prepare_retrieval", "elasticsearch")
    workflow.add_edge("prepare_retrieval", "qdrant")
    workflow.add_edge("prepare_retrieval", "neo4j")
    workflow.add_edge(["elasticsearch", "qdrant", "neo4j"], "fuse_evidence")
    workflow.add_edge("fuse_evidence", END)
    return workflow.compile()
