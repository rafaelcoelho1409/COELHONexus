"""LangGraph node wrappers for chapter-level book knowledge extraction."""
from __future__ import annotations

import json

import domains
from langgraph.runtime import Runtime

from ai.bd.agents.rag.graph_extractor import schemas
from ai.bd.graphs.rag.graph_extraction import domain, entities, params, states


async def extract_candidates(
    state: states.GraphExtractionState,
    runtime: Runtime[entities.GraphExtractionContext],
) -> dict:
    payload = json.dumps(
        {
            "section_title": state.get("section_title", ""),
            "prompt_version": runtime.context.prompt_version,
            "source_material": state.get("source_material", ""),
            "source_evidence": state.get("source_evidence", [])[:80],
            "validation_feedback": state.get("validation_issues", [])[:params.MAX_VALIDATION_FEEDBACK_ITEMS],
            "previous_candidates": state.get("candidate_extraction", {}),
        },
        ensure_ascii = False,
        separators = (",", ":"),
    )
    with domains.settings.llm_counter.service.llm_context(
        ns = "bd",
        run_id = runtime.context.run_id,
        node_id = "rag_graph_extractor",
        stage = "agentic_rag",
    ):
        result = await runtime.context.agent.ainvoke(
            {"messages": [{"role": "user", "content": payload}]},
            config = {"callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()]},
        )
    structured = result.get("structured_response") if isinstance(result, dict) else None
    extraction = schemas.GraphExtraction.model_validate(structured)
    return {
        "candidate_extraction": extraction.model_dump(mode = "json"),
        "attempt": state.get("attempt", 0) + 1,
    }


def validate_evidence(state: states.GraphExtractionState) -> dict:
    candidate = schemas.GraphExtraction.model_validate(state.get("candidate_extraction", {}))
    accepted, issues = domain.validate_extraction(candidate, state.get("source_evidence", []))
    return {
        "accepted_extraction": accepted.model_dump(mode = "json"),
        "validation_issues": issues,
    }


def retry_if_needed(state: states.GraphExtractionState) -> str:
    if state.get("validation_issues") and state.get("attempt", 0) <= params.MAX_EVIDENCE_RETRIES:
        return "retry"
    return "done"
