"""Pure candidate-to-result validation for YCS transcript extraction."""
from __future__ import annotations
from .... import agents, graphs


def validate_evidence(
    state: graphs.ingestion.graph_extraction.states.GraphExtractionState,
) -> dict:
    """Convert and validate candidates, then return accepted evidence and issues."""
    candidate = agents.schemas.GraphExtraction.model_validate(
        state.get("candidate_extraction", {}),
    )
    accepted, issues = graphs.ingestion.graph_extraction.domain.validate_extraction(
        candidate,
        state.get("transcript", ""),
    )
    return {
        "accepted_extraction": accepted.model_dump(mode = "json"),
        "validation_issues": issues,
    }
