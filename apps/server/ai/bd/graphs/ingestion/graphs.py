"""Deterministic Book Distiller ingestion graph builder."""
from __future__ import annotations
from . import entities, states
from ... import nodes

from typing import Any

from langgraph.graph import END, START, StateGraph


def ingestion_graph(checkpointer: Any | None = None):
    """Compile the full-book, versioned and review-aware ingestion workflow. What a run needs (pages, agents, progress) reaches
    the nodes at run time through the graph's `context` (`IngestionDependencies`), so building the graph needs nothing but a checkpointer."""
    workflow = StateGraph(states.IngestionRunState, context_schema = entities.IngestionDependencies)
    workflow.add_node("audit_batch", nodes.ingestion.audit_batch.node.audit_pages)
    workflow.add_node("plan_repairs", nodes.ingestion.plan_repairs.node.plan_repairs)
    workflow.add_node("repair_pages", nodes.ingestion.repair_pages.node.repair_pages)
    workflow.add_node("normalize_metadata", nodes.ingestion.metadata_normalization.node.normalize_metadata)
    workflow.add_node("publish", nodes.ingestion.publish.node.publish)
    workflow.add_edge(START, "audit_batch")
    workflow.add_edge("audit_batch", "plan_repairs")
    workflow.add_edge("plan_repairs", "repair_pages")
    workflow.add_edge("repair_pages", "normalize_metadata")
    workflow.add_edge("normalize_metadata", "publish")
    workflow.add_edge("publish", END)
    return workflow.compile(checkpointer = checkpointer)
