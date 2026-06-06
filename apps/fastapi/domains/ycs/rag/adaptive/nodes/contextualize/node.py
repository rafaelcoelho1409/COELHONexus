"""ycs/rag/adaptive/nodes/contextualize — CONTEXTUALIZE node.

If conversation history exists, rewrite the question to be standalone
("tell me more about that" → "tell me more about Elon Musk's views on
AGI"). Short-circuits with zero LLM cost when history is empty.

Direct port of deprecated `graphs/youtube/adaptive.py:L64-92`."""
from __future__ import annotations

from ....domain import strip_think_tags
from ...params import MAX_HISTORY_ANSWER_CHARS, MAX_HISTORY_TURNS
from ...state import AdaptiveRAGState
from .prompts import CONTEXTUALIZE_PROMPT


async def contextualize_question(state: AdaptiveRAGState, llm) -> dict:
    """Rewrite the question when prior history exists."""
    history = state.get("conversation_history") or []
    if not history:
        return {}

    parts: list[str] = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        parts.append(
            f"Q: {turn['question']}\n"
            f"A: {turn['answer'][:MAX_HISTORY_ANSWER_CHARS]}"
        )
    formatted = "\n---\n".join(parts)

    chain = CONTEXTUALIZE_PROMPT | llm
    try:
        response = await chain.ainvoke({
            "history":  formatted,
            "question": state["question"],
        })
        rewritten = strip_think_tags(response.content)
        if rewritten and rewritten != state["question"]:
            return {"question": rewritten, "search_query": rewritten}
    except Exception:
        pass
    return {}
