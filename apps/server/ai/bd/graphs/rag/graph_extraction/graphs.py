"""Compile one source batch's candidate extraction, evidence check, and bounded retry."""
from __future__ import annotations

import langgraph.graph

from .... import nodes
from . import entities, states


def graph_extraction_graph():
    workflow = langgraph.graph.StateGraph(states.GraphExtractionState, context_schema = entities.GraphExtractionContext)
    workflow.add_node("extract_candidates", nodes.rag.graph_extraction.node.extract_candidates)
    workflow.add_node("validate_evidence", nodes.rag.graph_extraction.node.validate_evidence)
    workflow.add_edge(langgraph.graph.START, "extract_candidates")
    workflow.add_edge("extract_candidates", "validate_evidence")
    workflow.add_conditional_edges(
        "validate_evidence",
        nodes.rag.graph_extraction.node.retry_if_needed,
        {"retry": "extract_candidates", "done": langgraph.graph.END},
    )
    return workflow.compile()
