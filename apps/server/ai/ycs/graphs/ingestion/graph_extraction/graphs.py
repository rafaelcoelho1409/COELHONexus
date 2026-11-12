"""Compile the YCS per-transcript extraction and evidence-validation graph."""
from __future__ import annotations

from .... import nodes
from . import entities, states

import langgraph.graph


def graph_extraction_graph():
    """Extract candidates, validate evidence, and retry once with grounded feedback."""
    workflow = langgraph.graph.StateGraph(states.GraphExtractionState, context_schema = entities.GraphExtractionContext)
    workflow.add_node("extract_candidates", nodes.ingestion.graph_extraction.node.extract_candidates)
    workflow.add_node("validate_evidence", nodes.ingestion.graph_extraction.node.validate_evidence)
    workflow.add_edge(langgraph.graph.START, "extract_candidates")
    workflow.add_edge("extract_candidates", "validate_evidence")
    workflow.add_conditional_edges(
        "validate_evidence",
        nodes.ingestion.graph_extraction.node.retry_if_needed,
        {"retry": "extract_candidates", "done": langgraph.graph.END},
    )
    return workflow.compile()
