"""ycs/rag/adaptive/nodes/contextualize — CONTEXTUALIZE node.

If conversation history exists, rewrite the question to be standalone
("tell me more about that" → "tell me more about Elon Musk's views on
AGI"). Short-circuits with zero LLM cost when history is empty.
"""
from __future__ import annotations
import domains
from domains.ycs.runtime.observability.service import traced
from .... import domain, service
from ... import params, state
from . import prompts

import asyncio


@traced("rag.contextualize")
async def contextualize_question(state: state.AdaptiveRAGState, llm) -> dict:
    """Rewrite the question when prior history exists."""
    history = state.get("conversation_history") or []
    if not history:
        return {"contextualized": True}

    parts: list[str] = []
    for turn in history[-params.MAX_HISTORY_TURNS:]:
        parts.append(
            f"Q: {turn['question']}\n"
            f"A: {turn['answer'][:params.MAX_HISTORY_ANSWER_CHARS]}"
        )
    formatted = "\n---\n".join(parts)

    chain = prompts.CONTEXTUALIZE_PROMPT | llm
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "contextualize")
        response = await service.resilient_ainvoke(
            chain,
            {
                "history":  formatted,
                "question": state["question"],
            },
            operation    = "contextualize",
            timeout_s    = params.CONTEXTUALIZE_TIMEOUT_S,
            max_attempts = 2,
        )
        rewritten = domain.strip_think_tags(response.content)
        if rewritten and rewritten != state["question"]:
            return {
                "question":       rewritten,
                "search_query":   rewritten,
                "contextualized": True,
            }
    except (asyncio.TimeoutError, Exception):
        pass
    return {"contextualized": True}
