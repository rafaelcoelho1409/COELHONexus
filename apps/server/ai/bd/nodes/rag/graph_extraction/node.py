"""LangGraph adapters and routes for chapter-level knowledge extraction."""
from __future__ import annotations
from . import domain, service
from .... import graphs

from langgraph.runtime import Runtime


async def extract_candidates(
    graph_state: graphs.rag.graph_extraction.states.GraphExtractionState,
    runtime: Runtime[graphs.rag.graph_extraction.entities.GraphExtractionContext],
) -> dict:
    """Extract graph candidates under graph progress reporting when streamed."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "extract_candidates",
        lambda: service.extract_candidates(graph_state, runtime),
    )


async def validate_evidence(
    graph_state: graphs.rag.graph_extraction.states.GraphExtractionState,
) -> dict:
    """Validate extraction evidence under graph progress reporting when streamed."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "validate_evidence",
        lambda: domain.validate_evidence(graph_state),
    )


def retry_if_needed(graph_state: graphs.rag.graph_extraction.states.GraphExtractionState) -> str:
    if graph_state.get("validation_issues") and graph_state.get("attempt", 0) <= graphs.rag.graph_extraction.params.MAX_EVIDENCE_RETRIES:
        return "retry"
    return "done"
