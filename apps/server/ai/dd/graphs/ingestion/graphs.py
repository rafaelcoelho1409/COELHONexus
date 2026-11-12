"""Deterministic Docs Distiller page-ingestion graph builder."""
from __future__ import annotations
from . import entities, states
from ... import nodes

from typing import Any

from langgraph.graph import END, START, StateGraph


def ingestion_graph(checkpointer: Any | None = None):
    """Compile the fixed audit, normalize, then publish workflow. What a run needs (pages, artifacts, agents, progress) reaches the nodes
    at run time through the graph's `context` (`IngestionDependencies`), so building the graph needs nothing but a checkpointer."""
    workflow = StateGraph(states.IngestionState, context_schema = entities.IngestionDependencies)
    workflow.add_node("audit_pages", nodes.ingestion.audit_batch.node.audit_pages)
    workflow.add_node("normalize_pages", nodes.ingestion.normalize_pages.node.normalize_pages)
    workflow.add_node("publish", nodes.ingestion.publish.node.publish)
    workflow.add_edge(START, "audit_pages")
    workflow.add_edge("audit_pages", "normalize_pages")
    workflow.add_edge("normalize_pages", "publish")
    workflow.add_edge("publish", END)
    return workflow.compile(checkpointer = checkpointer)
