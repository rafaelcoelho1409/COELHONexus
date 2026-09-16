"""ycs/rag/adaptive/nodes/contextualize — CONTEXTUALIZE node.

If conversation history exists, rewrite the question to be standalone
("tell me more about that" → "tell me more about Elon Musk's views on
AGI"). Short-circuits with zero LLM cost when history is empty.
"""
from __future__ import annotations

import asyncio

from domains.ycs.rag.llm_call import resilient_ainvoke
from domains.ycs.runtime.llm_counter import set_node as _llm_set_node
from domains.ycs.runtime.observability import traced

from ....domain import strip_think_tags
from ...params import MAX_HISTORY_ANSWER_CHARS, MAX_HISTORY_TURNS
from ...state import AdaptiveRAGState
from .prompts import CONTEXTUALIZE_PROMPT


# 2026-09-15: 45 → 30s tiering — failure degrades to skipping the
# rewrite (uses the question as-is), so fail fast.
_CONTEXTUALIZE_TIMEOUT_S = 30.0


@traced("rag.contextualize")
async def contextualize_question(state: AdaptiveRAGState, llm) -> dict:
    """Rewrite the question when prior history exists."""
    history = state.get("conversation_history") or []
    if not history:
        return {"contextualized": True}

    parts: list[str] = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        parts.append(
            f"Q: {turn['question']}\n"
            f"A: {turn['answer'][:MAX_HISTORY_ANSWER_CHARS]}"
        )
    formatted = "\n---\n".join(parts)

    chain = CONTEXTUALIZE_PROMPT | llm
    try:
        _llm_set_node(node = "contextualize")
        response = await resilient_ainvoke(
            chain,
            {
                "history":  formatted,
                "question": state["question"],
            },
            operation    = "contextualize",
            timeout_s    = _CONTEXTUALIZE_TIMEOUT_S,
            max_attempts = 2,
        )
        rewritten = strip_think_tags(response.content)
        if rewritten and rewritten != state["question"]:
            return {
                "question":       rewritten,
                "search_query":   rewritten,
                "contextualized": True,
            }
    except (asyncio.TimeoutError, Exception):
        pass
    return {"contextualized": True}
