"""LangGraph node wrappers for YCS transcript graph extraction."""
from __future__ import annotations

import domains

from ai.ycs.agents import schemas, service
from ai.ycs.agents.ingestion.graph_extractor import params as agent_params
from ai.ycs.graphs.ingestion.graph_extraction import domain, entities, states
from ai.ycs.graphs.ingestion.graph_extraction import params as graph_params

import json

from langgraph.runtime import Runtime


async def extract_candidates(
    state: states.GraphExtractionState,
    runtime: Runtime[entities.GraphExtractionContext],
) -> dict:
    if runtime.context.run_id:
        domains.settings.llm_counter.service.set_context(
            ns = "ycs",
            run_id = runtime.context.run_id,
            node_id = state.get("metadata", {}).get("video_id", ""),
        )
    payload = json.dumps({
        "video": state.get("metadata", {}),
        "transcript": state.get("transcript", ""),
        "validation_feedback": (state.get("validation_issues", []) or [])[
            :graph_params.MAX_VALIDATION_FEEDBACK_ITEMS
        ],
        "previous_candidates": state.get("candidate_extraction", {}),
    }, ensure_ascii = False, separators = (",", ":"))
    result = await service.ask(
        runtime.context.agent,
        schemas.GraphExtraction,
        payload,
        attempts = agent_params.MAX_ATTEMPTS,
        wait_base_s = agent_params.RETRY_WAIT_BASE_S,
        config = (
            {"callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()]}
            if runtime.context.run_id else None
        ),
        check = lambda answer: len(answer.entities) <= 150 and len(answer.relationships) <= 300,
    )
    return {
        "candidate_extraction": result.model_dump(mode = "json"),
        "attempt": state.get("attempt", 0) + 1,
    }


def validate_evidence(state: states.GraphExtractionState) -> dict:
    candidate = schemas.GraphExtraction.model_validate(state.get("candidate_extraction", {}))
    accepted, issues = domain.validate_extraction(candidate, state.get("transcript", ""))
    return {
        "accepted_extraction": accepted.model_dump(mode = "json"),
        "validation_issues": issues,
    }


def retry_if_needed(state: states.GraphExtractionState) -> str:
    if state.get("validation_issues") and state.get("attempt", 0) <= graph_params.MAX_EVIDENCE_RETRIES:
        return "retry"
    return "done"
