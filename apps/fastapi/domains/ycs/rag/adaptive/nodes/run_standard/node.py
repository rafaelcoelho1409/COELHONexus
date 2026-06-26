"""ycs/rag/adaptive/nodes/run_standard — STANDARD-path delegation node.

Builds a channel-scoped STANDARD sub-graph from the parent state's
`channel_ids` and invokes it. Maps the sub-graph's `YouTubeRAGState`
fields back to the parent `AdaptiveRAGState`.
2026-06-16 — budget fix. The STANDARD path keeps `max_retries=3` (the
user-facing default), so its recursion budget must come from
`standard/params.py::DEFAULT_RECURSION_LIMIT` (30 — sized for that
max_retries) rather than the sub-agent budget. The previous reuse of
`SUBAGENT_RECURSION_LIMIT` (12, sized for sub-agents'
`max_retries=1`) blew the limit mid-loop on STANDARD requests and
surfaced as `Pipeline error: Recursion limit of 12 reached without
hitting a stop condition` after ~5 min of work. We now ALSO thread
`max_retries` from the parent `RunnableConfig` so a user-supplied
override actually reaches the sub-graph's conditional edges instead
of silently defaulting back to 3."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from domains.ycs.runtime.observability import traced

from ...state import AdaptiveRAGState
from ....standard.params import DEFAULT_MAX_RETRIES, DEFAULT_RECURSION_LIMIT


@traced("rag.run_standard")
async def run_standard_pipeline(
    state: AdaptiveRAGState,
    standard_graph,
    config: RunnableConfig | None = None,
) -> dict:
    """Invoke the STANDARD pipeline as a sub-graph.

    `config` is auto-injected by LangGraph when the parent graph's node
    closure forwards it (see `adaptive/graph.py::_run_standard`). We
    read the user's `max_retries` from `configurable` and forward it to
    the scoped sub-graph so the rewrite-loop cap matches the parent
    request, not the STANDARD pipeline's hard-coded default."""
    initial = {
        "question":             state["question"],
        "thread_id":            state.get("thread_id", ""),
        "route":                state.get("route", "search"),
        "mode":                 state.get("mode", "standard") or "standard",
        "documents":            [],
        "generation":           "",
        "retry_count":          0,
        "search_query":         state.get("search_query") or state["question"],
        "grounded":             False,
        "citations":            [],
        "retrieval_sources":    [],
        # Thread the parent's history into the sub-graph so STANDARD's
        # generate node can ground a follow-up against prior turns.
        "conversation_history": state.get("conversation_history", []),
    }
    max_retries = DEFAULT_MAX_RETRIES
    if config is not None:
        max_retries = (
            (config.get("configurable") or {}).get("max_retries")
            or DEFAULT_MAX_RETRIES
        )
    sub_config = {
        "recursion_limit": DEFAULT_RECURSION_LIMIT,
        "configurable":    {"max_retries": max_retries},
    }
    try:
        result = await standard_graph.ainvoke(initial, config = sub_config)
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
