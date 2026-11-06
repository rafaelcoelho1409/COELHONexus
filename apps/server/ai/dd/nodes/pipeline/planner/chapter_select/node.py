"""chapter_select graph node: runs the selection subgraph (deterministic selection, then a curator agent per oversize chapter)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced("chapter_select")
async def chapter_select(
    state: graphs.pipeline.planner.state.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.context.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.chapter_select.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "chapter_proposals_ref": state.get("chapter_proposals_ref"),
            "chapter_doc_assignments_ref": state.get("chapter_doc_assignments_ref"),
        },
        {"max_concurrency": params.CURATOR_CONCURRENCY},
        context = runtime.context,
    )
    return {
        "chapter_plan_ref": result.get("chapter_plan_ref"),
        "select_stats": result.get("select_stats") or {},
    }
