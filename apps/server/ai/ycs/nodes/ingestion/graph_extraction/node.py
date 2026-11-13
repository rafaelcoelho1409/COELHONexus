"""LangGraph node wrappers for YCS transcript graph extraction."""
from __future__ import annotations
from . import domain, service
from .... import graphs

from langgraph.runtime import Runtime


async def extract_candidates(
    state: graphs.ingestion.graph_extraction.states.GraphExtractionState,
    runtime: Runtime[graphs.ingestion.graph_extraction.entities.GraphExtractionContext],
) -> dict:
    """Extract graph candidates through the extraction service."""
    return await service.extract_candidates(state, runtime)


def validate_evidence(state: graphs.ingestion.graph_extraction.states.GraphExtractionState) -> dict:
    """Validate extracted candidates against transcript evidence."""
    return domain.validate_evidence(state)


def retry_if_needed(state: graphs.ingestion.graph_extraction.states.GraphExtractionState) -> str:
    """Retry extraction while validation issues remain and retries are available."""
    if (
        state.get("validation_issues")
        and state.get("attempt", 0) <= graphs.ingestion.graph_extraction.params.MAX_EVIDENCE_RETRIES
    ):
        return "retry"
    return "done"
