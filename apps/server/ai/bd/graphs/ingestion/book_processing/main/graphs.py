"""Compile the root Book Distiller ingestion workflow."""
from __future__ import annotations
from .. import entities, states, subgraphs
from ..... import nodes

from typing import Any

from langgraph.graph import END, START, StateGraph


def ingestion_graph(checkpointer: Any | None = None):
    """Compile audit, parallel content repair and metadata, enrichment, and finalization."""
    workflow = StateGraph(
        states.IngestionRunState,
        context_schema = entities.IngestionDependencies,
    )
    workflow.add_node("audit_batch", nodes.ingestion.book_processing.main.audit_batch.node.audit_pages)
    workflow.add_node(
        "normalize_metadata",
        nodes.ingestion.book_processing.main.normalize_metadata.node.normalize_metadata,
    )
    workflow.add_node("enrich_visuals", nodes.ingestion.book_processing.main.enrich_visuals.node.enrich_visuals)
    # Subgraph
    workflow.add_node("content_repair", subgraphs.content_repair.graphs.content_repair_graph())
    # Subgraph
    workflow.add_node("book_finalization", subgraphs.book_finalization.graphs.book_finalization_graph())

    workflow.add_edge(START, "audit_batch")
    workflow.add_edge("audit_batch", "content_repair")
    workflow.add_edge("audit_batch", "normalize_metadata")
    workflow.add_edge(["content_repair", "normalize_metadata"], "enrich_visuals")
    workflow.add_edge("enrich_visuals", "book_finalization")
    workflow.add_edge("book_finalization", END)
    return workflow.compile(checkpointer = checkpointer)
