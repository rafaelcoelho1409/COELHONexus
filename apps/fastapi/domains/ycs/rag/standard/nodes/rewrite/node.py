"""ycs/rag/standard/nodes/rewrite — REWRITE node.

Expands or rephrases the previous search query for a retry retrieval.
`<think>` blocks stripped from the model output. Increments
`retry_count` so the conditional edges can cap the loop.
"""
from __future__ import annotations

import asyncio

from domains.ycs.rag.llm_call import resilient_ainvoke
from domains.ycs.runtime.observability import record_rewrite, traced

from ....domain import strip_think_tags
from ...state import YouTubeRAGState
from .prompts import REWRITE_PROMPT


# 2026-09-15: 30 → 20s tiering — failure falls back to
# "{question} (expanded)", so fail fast.
_REWRITE_TIMEOUT_S = 20.0


@traced("rag.rewrite")
async def rewrite_query(state: YouTubeRAGState, llm) -> dict:
    """Expand/rephrase the query for better retrieval."""
    record_rewrite(
        route = str(state.get("route") or "unknown"),
        mode = str(state.get("mode") or "standard"),
    )
    chain = REWRITE_PROMPT | llm
    try:
        response = await resilient_ainvoke(
            chain,
            {
                "question":     state["question"],
                "search_query": state.get("search_query") or state["question"],
            },
            operation    = "rewrite",
            timeout_s    = _REWRITE_TIMEOUT_S,
            max_attempts = 2,
        )
        new_query = strip_think_tags(response.content)
    except (asyncio.TimeoutError, Exception):
        new_query = f"{state['question']} (expanded)"
    return {
        "search_query": new_query,
        "retry_count":  state.get("retry_count", 0) + 1,
    }
