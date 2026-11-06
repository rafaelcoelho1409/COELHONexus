"""chapter_propose graph node: runs the proposal subgraph (section analysts in parallel, then the architect and its score gate)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced("chapter_propose")
async def chapter_propose(
    state: graphs.pipeline.planner.state.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.context.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.chapter_propose.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "raw_files": state.get("raw_files") or [],
            "relevant_files": state.get("relevant_files") or [],
            "doc_distill_ref": state.get("doc_distill_ref"),
        },
        {"max_concurrency": params.ANALYST_CONCURRENCY},
        context = runtime.context,
    )
    return {
        "chapter_proposals_ref": result.get("chapter_proposals_ref"),
        "propose_stats": result.get("propose_stats") or {},
    }
