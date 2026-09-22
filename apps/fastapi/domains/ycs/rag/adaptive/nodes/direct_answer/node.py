"""ycs/rag/adaptive/nodes/direct_answer — FAST node.

Single LLM call, no retrieval. Returns a synthetic `grounded=True`
+ empty citations so the response envelope shape stays consistent
with STANDARD / DEEP.
"""
from __future__ import annotations
import domains
from domains.ycs.runtime.observability.service import traced
from .... import domain, service
from ... import params, state
from . import prompts

import asyncio


@traced("rag.direct_answer")
async def direct_answer(state: state.AdaptiveRAGState, llm) -> dict:
    """FAST path: direct LLM answer without retrieval."""
    chain = prompts.DIRECT_ANSWER_PROMPT | llm
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "direct_answer")
        # 2026-09-15: hedged racer (Tail-at-Scale) instead of plain
        # retry — primary + one duplicate fired at +20s, first wins.
        # Healthy path pays zero extra; slow tail gets raced, not
        # waited out twice. Raises last error unchanged, so the
        # handlers below behave exactly as before.
        response = await service.hedged_ainvoke(
            chain,
            {
                "question": state["question"],
                "history":  domain.history_to_messages(state.get("conversation_history")),
            },
            operation = "direct_answer",
            timeout_s = params.DIRECT_ANSWER_TIMEOUT_S,
        )
        return {
            "generation":        domain.strip_think_tags(response.content),
            "grounded":          True,
            "citations":         [],
            "retrieval_sources": [],
        }
    except asyncio.TimeoutError:
        return {
            "generation": (
                f"The model didn't respond within "
                f"{int(params.DIRECT_ANSWER_TIMEOUT_S)}s. Please retry."
            ),
            "grounded": False,
        }
    except Exception as e:
        return {"generation": f"Error: {e}", "grounded": False}
