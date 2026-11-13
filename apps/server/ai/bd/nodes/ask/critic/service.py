"""Evidence verification and fallback responses for BD Ask."""
from __future__ import annotations
import domains
from .... import agents, graphs

from langgraph.runtime import Runtime


async def verify_answer(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    domains.settings.llm_counter.service.set_node(node = "critic")
    try:
        result = await agents.ask.service.invoke(
            runtime.context.critic,
            agents.ask.schemas.EvidenceAssessment,
            {
                "question": state.get("question", ""),
                "answer": state.get("generation", ""),
                "evidence": state.get("documents", []),
            },
        )
    except Exception:
        return {
            "grounded": False,
            "confidence_score": 0.0,
            "verification_status": "unknown",
            "generation": "I could not verify an answer from the indexed book evidence. Please try a narrower question.",
            "citations": [],
        }
    if not result.claims_supported:
        return {
            "grounded": False,
            "confidence_score": result.confidence_score,
            "verification_status": "unsupported",
            "generation": "The indexed book evidence did not support a reliable answer to this question.",
            "citations": [],
        }
    return {
        "grounded": True,
        "confidence_score": result.confidence_score,
        "verification_status": "supported",
    }
