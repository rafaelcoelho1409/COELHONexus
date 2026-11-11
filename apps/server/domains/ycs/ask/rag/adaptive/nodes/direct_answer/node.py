"""ycs/ask/rag/adaptive/nodes/direct_answer — corpus-free conversation node.

Single LLM call, no retrieval. The classifier may route greetings and
app-meta questions here; factual video questions always go through a
retrieval path, even when their effort mode is FAST.
"""
from __future__ import annotations
import domains
from domains.ycs.ask.observability.service import traced
from . import prompts
from ... import params, state
from .... import domain, service

import asyncio


@traced("rag.direct_answer")
async def direct_answer(state: state.AdaptiveRAGState, llm) -> dict:
    """Answer a conversational turn without making corpus claims."""
    chain = service.resolve_prompt(prompts.DIRECT_ANSWER_PROMPT, "ycs.rag.direct_answer") | llm
    try:
        domains.settings.llm_counter.service.set_node(node = "direct_answer")
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
            "grounded":          False,
            "citations":         [],
            "retrieval_sources": [],
        }
    except asyncio.TimeoutError:
        return {
            "generation": (
                "Error generating answer: the model did not respond within "
                f"{int(params.DIRECT_ANSWER_TIMEOUT_S)}s. Please retry."
            ),
            "grounded": False,
        }
    except Exception as e:
        # The "Error generating answer:" prefix is what the web client reads as a failed turn (see the generate node).
        return {"generation": f"Error generating answer: {e}", "grounded": False}
