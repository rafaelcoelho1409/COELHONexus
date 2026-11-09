"""Compile the YCS per-transcript extraction and evidence-validation graph."""
from __future__ import annotations

from . import entities, states

from langgraph.graph import END, START, StateGraph


def graph_extraction_graph():
    """Extract candidates, validate evidence, and retry once with grounded feedback."""
    from ai.ycs.nodes.ingestion.graph_extraction import node

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
