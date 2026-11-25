"""Compile page classification, repair planning, and page repair."""
from __future__ import annotations
from ... import entities, states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def content_repair_graph():
    """Compile the sequential content classification and repair workflow."""
    workflow = StateGraph(
        states.IngestionRunState,
        context_schema = entities.IngestionDependencies,
    )
    workflow.add_node(
        "classify_content",
        nodes.ingestion.subgraphs.content_repair.classify_content.node.classify_content,
    )
    workflow.add_node(
        "plan_repairs",
        nodes.ingestion.subgraphs.content_repair.plan_repairs.node.plan_repairs,
    )
    workflow.add_node(
        "repair_pages",
        nodes.ingestion.subgraphs.content_repair.repair_pages.node.repair_pages,
    )

    workflow.add_edge(START, "classify_content")
    workflow.add_edge("classify_content", "plan_repairs")
    workflow.add_edge("plan_repairs", "repair_pages")
    workflow.add_edge("repair_pages", END)
    return workflow.compile()
