"""ycs/rag/adaptive/nodes/subagent — DEEP-path fan-out target.

Each parallel sub-agent runs the STANDARD pipeline against ONE
sub-question. Receives a minimal `payload` dict (not the full parent
state) per LangGraph `Send()` semantics. Returns into `sub_results`
via the `operator.add` reducer declared in `state.py`.

Direct port of deprecated `graphs/youtube/adaptive.py:L226-265`."""
from __future__ import annotations

from ...params import SUBGRAPH_RECURSION_LIMIT


async def run_subagent(payload: dict, standard_graph) -> dict:
    """Run the STANDARD pipeline for one sub-question, then project the
    result into a `sub_results` entry."""
    sub_q = payload["sub_question"]
    initial = {
        "question":          sub_q,
        "documents":         [],
        "generation":        "",
        "retry_count":       0,
        "search_query":      sub_q,
        "grounded":          False,
        "citations":         [],
        "retrieval_sources": [],
    }
    config = {"recursion_limit": SUBGRAPH_RECURSION_LIMIT}
    try:
        result = await standard_graph.ainvoke(initial, config = config)
    except Exception as e:
        result = {
            "generation":        f"Subagent error: {e}",
            "citations":         [],
            "grounded":          False,
            "retrieval_sources": [],
        }
    return {
        "sub_results": [{
            "sub_question":      sub_q,
            "answer":            result.get("generation", ""),
            "citations":         result.get("citations", []),
            "grounded":          result.get("grounded", False),
            "retrieval_sources": result.get("retrieval_sources", []),
        }],
    }
