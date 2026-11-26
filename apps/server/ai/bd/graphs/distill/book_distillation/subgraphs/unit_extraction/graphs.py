"""Compile learning unit preparation, extraction, and verification."""
from __future__ import annotations
from ... import entities, service, states
from ...... import nodes

from langgraph.graph import END, START, StateGraph


def unit_extraction_graph():
    """Compile the complete source-grounded learning unit extraction workflow."""
    workflow = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
    workflow.add_node(
        "prepare_units",
        nodes.distill.book_distillation.subgraphs.unit_extraction.prepare_units.node.prepare_units,
    )
    workflow.add_node(
        "extract_unit",
        nodes.distill.book_distillation.subgraphs.unit_extraction.extract_unit.node.extract_unit,
        retry_policy = service.provider_retry_policy(),
    )
    workflow.add_node(
        "verify_unit",
        nodes.distill.book_distillation.subgraphs.unit_extraction.verify_unit.node.verify_unit,
        retry_policy = service.provider_retry_policy(),
    )

    workflow.add_edge(START, "prepare_units")
    workflow.add_conditional_edges(
        "prepare_units",
        nodes.distill.book_distillation.subgraphs.unit_extraction.prepare_units.node.dispatch_units,
        {
            "extract_unit": "extract_unit",
            "prepare_chapters": END,
        },
    )
    workflow.add_conditional_edges(
        "extract_unit",
        nodes.distill.book_distillation.subgraphs.unit_extraction.verify_unit.node.dispatch_units,
        {
            "verify_unit": "verify_unit",
            "prepare_chapters": END,
        },
    )
    workflow.add_edge("verify_unit", END)
    return workflow.compile()
