"""ycs/rag/adaptive/nodes/direct_answer — FAST node.

Single LLM call, no retrieval. Returns a synthetic `grounded=True`
+ empty citations so the response envelope shape stays consistent
with STANDARD / DEEP.
"""
from __future__ import annotations

import asyncio

from domains.ycs.rag.llm_call import hedged_ainvoke
from domains.ycs.runtime.observability import traced

from ....domain import history_to_messages, strip_think_tags
from ...state import AdaptiveRAGState
from .prompts import DIRECT_ANSWER_PROMPT


# FAST path — tighter ceiling than STANDARD's `generate` because there
# is no retrieval to wait for. If a fast answer doesn't come back inside
# 90 s the model is hung; better to surface an error than spin forever.
_DIRECT_ANSWER_TIMEOUT_S = 90.0


@traced("rag.direct_answer")
async def direct_answer(state: AdaptiveRAGState, llm) -> dict:
    """FAST path: direct LLM answer without retrieval."""
    chain = DIRECT_ANSWER_PROMPT | llm
    try:
        # 2026-09-15: hedged racer (Tail-at-Scale) instead of plain
        # retry — primary + one duplicate fired at +20s, first wins.
        # Healthy path pays zero extra; slow tail gets raced, not
        # waited out twice. Raises last error unchanged, so the
        # handlers below behave exactly as before.
        response = await hedged_ainvoke(
            chain,
            {
                "question": state["question"],
                "history":  history_to_messages(state.get("conversation_history")),
            },
            operation = "direct_answer",
            timeout_s = _DIRECT_ANSWER_TIMEOUT_S,
        )
        return {
            "generation":        strip_think_tags(response.content),
            "grounded":          True,
            "citations":         [],
            "retrieval_sources": [],
        }
    except asyncio.TimeoutError:
        return {
            "generation": (
                f"The model didn't respond within "
                f"{int(_DIRECT_ANSWER_TIMEOUT_S)}s. Please retry."
            ),
            "grounded": False,
        }
    except Exception as e:
        return {"generation": f"Error: {e}", "grounded": False}
