"""Book-evidence synthesis and citation formatting nodes."""
from __future__ import annotations

import re

import ai
import domains

from langgraph.runtime import Runtime


async def generate_answer(state: dict, runtime: Runtime[ai.bd.graphs.ask.adaptive.entities.AskContext]) -> dict:
    domains.settings.llm_counter.service.set_node(node = "generate" if state.get("mode") != "deep" else "synthesize")
    documents = state.get("documents") or []
    answer = await ai.bd.agents.ask.service.invoke(
        runtime.context.answerer,
        ai.bd.agents.ask.schemas.EvidenceAnswer,
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


def format_citations(state: dict, runtime: Runtime[ai.bd.graphs.ask.adaptive.entities.AskContext]) -> dict:
    documents = state.get("documents") or []
    requested = set(state.get("evidence_ids") or [])
    selected = [item for item in documents if item.get("id") in requested]
    if not selected and state.get("grounded"):
        selected = documents[:3]
    citations = []
    seen = set()
    for item in selected:
        citation = item.get("citation") or {}
        key = (citation.get("book_id"), citation.get("page"), citation.get("title"), citation.get("source"))
        if key not in seen:
            seen.add(key)
            citations.append(citation)
    answer = re.sub(
        r"\[(?:Page:\s*\d+|Source:\s*[^\]]+)\]",
        "",
        str(state.get("generation", "")),
        flags = re.IGNORECASE,
    ).strip()
    if citations:
        markers = [
            f"[Page: {item['page']}]" if item.get("page") else f"[Source: {item.get('title', 'Book source')}]"
            for item in citations
        ]
        answer = f"{answer.rstrip()}\n\nSources: {' '.join(markers)}"
    return {"generation": answer, "citations": citations}
