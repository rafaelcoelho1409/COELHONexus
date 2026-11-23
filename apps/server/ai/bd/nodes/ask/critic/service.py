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
    documents = state.get("documents") or []
    document_ids = {str(item.get("id")) for item in documents if item.get("id")}
    evidence_ids = [str(item) for item in state.get("evidence_ids", [])]
    if any(item not in document_ids for item in evidence_ids) or (
        state.get("retrieval_sufficient") and not evidence_ids
    ):
        return {
            "grounded": False,
            "confidence_score": 0.0,
            "verification_status": "invalid_evidence_references",
            "generation": "I could not verify an answer from the cited book or search evidence. Please try a narrower question.",
            "citations": [],
        }
    try:
        result = await agents.ask.service.invoke(
            runtime.context.critic,
            agents.ask.schemas.EvidenceAssessment,
            {
                "question": state.get("question", ""),
                "answer": state.get("generation", ""),
                "evidence_ids": evidence_ids,
                "evidence": documents,
            },
            skill_role = "ask_critic",
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
