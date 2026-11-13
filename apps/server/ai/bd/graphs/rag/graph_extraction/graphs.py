"""Compile candidate extraction, evidence validation, and bounded candidate repair for one source batch."""
from __future__ import annotations

from .... import nodes
from . import entities, states

from langgraph.graph import END, START, StateGraph


def graph_extraction_graph():
    """Compile the context-backed graph extraction workflow."""
    workflow = StateGraph(
        states.GraphExtractionState,
        context_schema = entities.GraphExtractionContext,
    )
    workflow.add_node("extract_candidates", nodes.rag.graph_extraction.node.extract_candidates)
    workflow.add_node("validate_evidence", nodes.rag.graph_extraction.node.validate_evidence)
    workflow.add_edge(START, "extract_candidates")
    workflow.add_edge("extract_candidates", "validate_evidence")
    workflow.add_conditional_edges(
        "validate_evidence",
        nodes.rag.graph_extraction.node.retry_if_needed,
        {"retry": "extract_candidates", "done": END},
    )
    return workflow.compile()
