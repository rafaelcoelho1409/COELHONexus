"""Compile processed-book materialization, contents reconciliation, and publication."""
from __future__ import annotations
from ... import entities, states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def book_finalization_graph():
    """Compile the final processed-book workflow."""
    workflow = StateGraph(
        states.IngestionRunState,
        context_schema = entities.IngestionDependencies,
    )
    workflow.add_node(
        "materialize_processed_book",
        nodes.ingestion.subgraphs.book_finalization.materialize_processed_book.node.materialize_processed_book,
    )
    workflow.add_node(
        "reconcile_contents",
        nodes.ingestion.subgraphs.book_finalization.reconcile_contents.node.reconcile_contents,
    )
    workflow.add_node(
        "publish",
        nodes.ingestion.subgraphs.book_finalization.publish.node.publish,
    )

    workflow.add_edge(START, "materialize_processed_book")
    workflow.add_edge("materialize_processed_book", "reconcile_contents")
    workflow.add_edge("reconcile_contents", "publish")
    workflow.add_edge("publish", END)
    return workflow.compile()
