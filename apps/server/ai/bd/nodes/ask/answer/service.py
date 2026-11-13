"""Book-evidence answer generation for BD Ask."""
from __future__ import annotations
import domains
from .... import agents, graphs

from langgraph.runtime import Runtime


async def generate_answer(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    domains.settings.llm_counter.service.set_node(node = "generate" if state.get("mode") != "deep" else "synthesize")
    documents = state.get("documents") or []
    answer = await agents.ask.service.invoke(
        runtime.context.answerer,
        agents.ask.schemas.EvidenceAnswer,
        {
            "book_id": runtime.context.book_id,
            "question": state.get("question", ""),
            "research_plan": state.get("research_plan", ""),
            "conversation_history": state.get("conversation_history", [])[-4:],
            "evidence": documents,
            "instruction": "The evidence list is empty" if not documents else "Answer only from these evidence records.",
        },
    )
    return {"generation": answer.answer, "evidence_ids": answer.evidence_ids}
