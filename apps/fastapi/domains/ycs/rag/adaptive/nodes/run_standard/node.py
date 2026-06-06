"""ycs/rag/adaptive/nodes/run_standard — STANDARD-path delegation node.

Builds a channel-scoped STANDARD sub-graph from the parent state's
`channel_ids` and invokes it. Maps the sub-graph's `YouTubeRAGState`
fields back to the parent `AdaptiveRAGState`.

Direct port of deprecated `graphs/youtube/adaptive.py:L152-182`."""
from __future__ import annotations

from ...params import SUBGRAPH_RECURSION_LIMIT
from ...state import AdaptiveRAGState


async def run_standard_pipeline(
    state: AdaptiveRAGState, standard_graph,
) -> dict:
    """Invoke the STANDARD pipeline as a sub-graph."""
    initial = {
        "question":          state["question"],
        "documents":         [],
        "generation":        "",
        "retry_count":       0,
        "search_query":      state.get("search_query") or state["question"],
        "grounded":          False,
        "citations":         [],
        "retrieval_sources": [],
    }
    config = {"recursion_limit": SUBGRAPH_RECURSION_LIMIT}
    try:
        result = await standard_graph.ainvoke(initial, config = config)
    except Exception as e:
        return {"generation": f"Pipeline error: {e}", "grounded": False}
    return {
        "generation":        result.get("generation", ""),
        "citations":         result.get("citations", []),
        "grounded":          result.get("grounded", False),
        "retrieval_sources": result.get("retrieval_sources", []),
        "retry_count":       result.get("retry_count", 0),
        "search_query":      result.get("search_query", state["question"]),
    }
