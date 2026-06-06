"""ycs/rag/adaptive/nodes/direct_answer — FAST node.

Single LLM call, no retrieval. Returns a synthetic `grounded=True`
+ empty citations so the response envelope shape stays consistent
with STANDARD / DEEP.

Direct port of deprecated `graphs/youtube/adaptive.py:L135-150`."""
from __future__ import annotations

from ....domain import strip_think_tags
from ...state import AdaptiveRAGState
from .prompts import DIRECT_ANSWER_PROMPT


async def direct_answer(state: AdaptiveRAGState, llm) -> dict:
    """FAST path: direct LLM answer without retrieval."""
    chain = DIRECT_ANSWER_PROMPT | llm
    try:
        response = await chain.ainvoke({"question": state["question"]})
        return {
            "generation":        strip_think_tags(response.content),
            "grounded":          True,
            "citations":         [],
            "retrieval_sources": [],
        }
    except Exception as e:
        return {"generation": f"Error: {e}", "grounded": False}
