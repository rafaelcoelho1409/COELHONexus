"""DeepAgent relevance grading and minimum-evidence gate for BD Ask."""
from __future__ import annotations

import ai
import domains

from langgraph.runtime import Runtime


async def grade_evidence(state: dict, runtime: Runtime[ai.bd.graphs.ask.adaptive.entities.AskContext]) -> dict:
    documents = state.get("documents") or []
    domains.settings.llm_counter.service.set_node(node = "grade_documents")
    if not documents:
        return {"retrieval_sufficient": False, "document_count": 0, "documents": []}
    try:
        assessment = await ai.bd.agents.ask.service.invoke(
            runtime.context.grader,
            ai.bd.agents.ask.schemas.EvidenceGrades,
            {
                "question": state.get("question", ""),
                "sub_questions": state.get("sub_questions", []),
                "evidence": [
                    {"id": item.get("id"), "text": item.get("text", "")[:1200], "source": item.get("source")}
                    for item in documents
                ],
            },
        )
        relevant_ids = set(assessment.relevant_evidence_ids)
        relevant = [item for item in documents if item.get("id") in relevant_ids]
        enough = bool(relevant) and assessment.coverage != "insufficient"
        return {
            "documents": relevant,
            "document_count": len(relevant),
            "retrieval_sufficient": enough,
            "retrieval_coverage": assessment.coverage,
        }
    except Exception:
        # A failed judge does not bless a retrieval set; the graph gets one bounded rewrite.
        return {"documents": [], "document_count": 0, "retrieval_sufficient": False, "retrieval_coverage": "unknown"}
