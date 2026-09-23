"""ycs/rag/standard/nodes/rewrite — REWRITE node.

Expands or rephrases the previous search query for a retry retrieval.
`<think>` blocks stripped from the model output. Increments
`retry_count` so the conditional edges can cap the loop.
"""
from __future__ import annotations
import domains
from domains.ycs.runtime.observability.service import traced
from .... import domain, service
from ... import params, state
from . import prompts

import asyncio


@traced("rag.rewrite")
async def rewrite_query(state: state.YouTubeRAGState, llm) -> dict:
    """Expand/rephrase the query for better retrieval."""
    domains.ycs.runtime.observability.metrics.record_rewrite(
        route = str(state.get("route") or "unknown"),
        mode = str(state.get("mode") or "standard"),
    )
    chain = service.resolve_prompt(prompts.REWRITE_PROMPT, "ycs.rag.rewrite") | llm
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "rewrite")
        response = await service.resilient_ainvoke(
            chain,
            {
                "question":     state["question"],
                "search_query": state.get("search_query") or state["question"],
            },
            operation    = "rewrite",
            timeout_s    = params.REWRITE_TIMEOUT_S,
            max_attempts = 2,
        )
        new_query = domain.strip_think_tags(response.content)
    except (asyncio.TimeoutError, Exception):
        new_query = f"{state['question']} (expanded)"
    return {
        "search_query": new_query,
        "retry_count":  state.get("retry_count", 0) + 1,
    }
