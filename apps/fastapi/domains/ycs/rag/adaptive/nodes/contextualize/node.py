"""ycs/rag/adaptive/nodes/contextualize — CONTEXTUALIZE node.

If conversation history exists, rewrite the question to be standalone
("tell me more about that" → "tell me more about Elon Musk's views on
AGI"). Short-circuits with zero LLM cost when history is empty.

Direct port of deprecated `graphs/youtube/adaptive.py:L64-92` +
2026-06-16 per-call timeout (previously uncapped — a contextualize
hang would block the entire adaptive graph at its entry point)."""
from __future__ import annotations

import asyncio

from domains.ycs.runtime.observability import traced

from ....domain import strip_think_tags
from ...params import MAX_HISTORY_ANSWER_CHARS, MAX_HISTORY_TURNS
from ...state import AdaptiveRAGState
from .prompts import CONTEXTUALIZE_PROMPT


# 45 s ceiling on the contextualize LLM. Short prompt (≤5 prior
# turns, each truncated to ~300 chars) + a single one-line rewrite;
# healthy models return in 2–10 s. 45 s leaves margin for the rotator
# to retry once.
_CONTEXTUALIZE_TIMEOUT_S = 45.0


@traced("rag.contextualize")
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
        response = await asyncio.wait_for(
            chain.ainvoke({
                "history":  formatted,
                "question": state["question"],
            }),
            timeout = _CONTEXTUALIZE_TIMEOUT_S,
        )
        rewritten = strip_think_tags(response.content)
        if rewritten and rewritten != state["question"]:
            return {"question": rewritten, "search_query": rewritten}
    except (asyncio.TimeoutError, Exception):
        pass
    return {}
