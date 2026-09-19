"""ycs/rag/standard/nodes/rewrite — REWRITE node.

Expands or rephrases the previous search query for a retry retrieval.
`<think>` blocks stripped from the model output. Increments
`retry_count` so the conditional edges can cap the loop.
"""
from __future__ import annotations

import asyncio

import domains
from domains.ycs.runtime.observability.service import traced

from .... import domain, service
from ... import state
from . import prompts


# 2026-09-15: 30 → 20s tiering — failure falls back to
# "{question} (expanded)", so fail fast.
_REWRITE_TIMEOUT_S = 20.0


@traced("rag.rewrite")
async def rewrite_query(state: state.YouTubeRAGState, llm) -> dict:
    """Expand/rephrase the query for better retrieval."""
    domains.ycs.runtime.observability.metrics.record_rewrite(
        route = str(state.get("route") or "unknown"),
        mode = str(state.get("mode") or "standard"),
    )
    chain = prompts.REWRITE_PROMPT | llm
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "rewrite")
        response = await service.resilient_ainvoke(
            chain,
            {
                "question":     state["question"],
                "search_query": state.get("search_query") or state["question"],
            },
            operation    = "rewrite",
            timeout_s    = _REWRITE_TIMEOUT_S,
            max_attempts = 2,
        )
        new_query = domain.strip_think_tags(response.content)
    except (asyncio.TimeoutError, Exception):
        new_query = f"{state['question']} (expanded)"
    return {
        "search_query": new_query,
        "retry_count":  state.get("retry_count", 0) + 1,
    }
