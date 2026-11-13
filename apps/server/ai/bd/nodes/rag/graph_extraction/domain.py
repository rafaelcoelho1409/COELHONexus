"""Pure candidate-to-result validation for the graph extraction node."""
from __future__ import annotations

from .... import agents, graphs


def validate_evidence(graph_state: graphs.rag.graph_extraction.states.GraphExtractionState) -> dict:
    candidate = agents.rag.graph_extractor.schemas.GraphExtraction.model_validate(
        graph_state.get("candidate_extraction", {}),
    )
    accepted, issues = graphs.rag.graph_extraction.domain.validate_extraction(
        candidate,
        graph_state.get("source_evidence", []),
    )
    return {
        "accepted_extraction": accepted.model_dump(mode = "json"),
        "validation_issues": issues,
    }
