"""Compile one source batch's candidate extraction, evidence check, and bounded retry."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from . import entities, states


def graph_extraction_graph():
    from ai.bd.nodes.rag.graph_extraction import node

    workflow = StateGraph(states.GraphExtractionState, context_schema = entities.GraphExtractionContext)
    workflow.add_node("extract_candidates", node.extract_candidates)
    workflow.add_node("validate_evidence", node.validate_evidence)
    workflow.add_edge(START, "extract_candidates")
    workflow.add_edge("extract_candidates", "validate_evidence")
    workflow.add_conditional_edges(
        "validate_evidence",
        node.retry_if_needed,
        {"retry": "extract_candidates", "done": END},
    )
    return workflow.compile()
