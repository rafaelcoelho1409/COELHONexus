"""LLM-backed extraction and evidence validation for YCS transcripts."""
from __future__ import annotations
import domains
from .... import agents, graphs

import json

from langgraph.runtime import Runtime


async def extract_candidates(
    state: graphs.ingestion.graph_extraction.states.GraphExtractionState,
    runtime: Runtime[graphs.ingestion.graph_extraction.entities.GraphExtractionContext],
) -> dict:
    """Extract bounded candidate entities and relationships from a transcript."""
    if runtime.context.run_id:
        domains.settings.llm_counter.service.set_context(
            ns = "ycs",
            run_id = runtime.context.run_id,
            node_id = state.get("metadata", {}).get("video_id", ""),
        )
    payload = json.dumps(
        {
            "video": state.get("metadata", {}),
            "transcript": state.get("transcript", ""),
            "validation_feedback": (state.get("validation_issues", []) or [])[
                :graphs.ingestion.graph_extraction.params.MAX_VALIDATION_FEEDBACK_ITEMS
            ],
            "previous_candidates": state.get("candidate_extraction", {}),
        },
        ensure_ascii = False,
        separators = (",", ":"),
    )
    result = await agents.service.ask(
        runtime.context.agent,
        agents.schemas.GraphExtraction,
        payload,
        attempts = agents.ingestion.graph_extractor.params.MAX_ATTEMPTS,
        wait_base_s = agents.ingestion.graph_extractor.params.RETRY_WAIT_BASE_S,
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
