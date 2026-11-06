"""off_topic graph node: runs the relevance subgraph (one judge per distinct page text, in parallel)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced("off_topic")
async def off_topic(
    state: graphs.pipeline.planner.state.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.context.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.off_topic.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "raw_files": state.get("raw_files") or [],
            "corpus_stats": state.get("corpus_stats") or {},
        },
        {"max_concurrency": params.JUDGE_CONCURRENCY},
        context = runtime.context,
    )
    return {
        "relevant_files": result.get("relevant_files") or [],
        "off_topic_stats": result.get("off_topic_stats") or {},
    }
