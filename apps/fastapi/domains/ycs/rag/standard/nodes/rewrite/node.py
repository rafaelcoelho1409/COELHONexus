"""ycs/rag/standard/nodes/rewrite — REWRITE node.

Expands or rephrases the previous search query for a retry retrieval.
`<think>` blocks stripped from the model output. Increments
`retry_count` so the conditional edges can cap the loop.

Direct port of deprecated `graphs/youtube/rag.py:L170-187`."""
from __future__ import annotations

from domains.ycs.runtime.observability import traced

from ....domain import strip_think_tags
from ...state import YouTubeRAGState
from .prompts import REWRITE_PROMPT


@traced("rag.rewrite")
async def rewrite_query(state: YouTubeRAGState, llm) -> dict:
    """Expand/rephrase the query for better retrieval."""
    chain = REWRITE_PROMPT | llm
    try:
        response = await chain.ainvoke({
            "question":     state["question"],
            "search_query": state.get("search_query") or state["question"],
        })
        new_query = strip_think_tags(response.content)
    except Exception:
        new_query = f"{state['question']} (expanded)"
    return {
        "search_query": new_query,
        "retry_count":  state.get("retry_count", 0) + 1,
    }
