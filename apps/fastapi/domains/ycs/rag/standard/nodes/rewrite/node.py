"""ycs/rag/standard/nodes/rewrite — REWRITE node.

Expands or rephrases the previous search query for a retry retrieval.
`<think>` blocks stripped from the model output. Increments
`retry_count` so the conditional edges can cap the loop.

Direct port of deprecated `graphs/youtube/rag.py:L170-187` +
2026-06-16 per-call timeout (previously uncapped — a rewrite hang
would block the standard sub-graph forever)."""
from __future__ import annotations

import asyncio

from domains.ycs.runtime.observability import record_rewrite, traced

from ....domain import strip_think_tags
from ...state import YouTubeRAGState
from .prompts import REWRITE_PROMPT


# 30 s ceiling on the rewrite LLM. Tiny prompt + single short
# completion, no retrieval involved; if a healthy arm can't return
# in 30 s the rotator should already have fallen over.
_REWRITE_TIMEOUT_S = 30.0


@traced("rag.rewrite")
async def rewrite_query(state: YouTubeRAGState, llm) -> dict:
    """Expand/rephrase the query for better retrieval."""
    record_rewrite(
        route = str(state.get("route") or "unknown"),
        mode = str(state.get("mode") or "standard"),
    )
    chain = REWRITE_PROMPT | llm
    try:
        response = await asyncio.wait_for(
            chain.ainvoke({
                "question":     state["question"],
                "search_query": state.get("search_query") or state["question"],
            }),
            timeout = _REWRITE_TIMEOUT_S,
        )
        new_query = strip_think_tags(response.content)
    except (asyncio.TimeoutError, Exception):
        new_query = f"{state['question']} (expanded)"
    return {
        "search_query": new_query,
        "retry_count":  state.get("retry_count", 0) + 1,
    }
