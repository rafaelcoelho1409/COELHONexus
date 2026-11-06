"""chapter_assign graph node: runs the scoring subgraph (one scorer per page, in parallel, then a sweep over the pages that fell back)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced("chapter_assign")
async def chapter_assign(
    state: graphs.pipeline.planner.state.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.context.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.chapter_assign.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "raw_files": state.get("raw_files") or [],
            "relevant_files": state.get("relevant_files") or [],
            "chapter_proposals_ref": state.get("chapter_proposals_ref"),
        },
        {"max_concurrency": params.CONCURRENCY},
        context = runtime.context,
    )
    return {
        "chapter_doc_assignments_ref": result.get("chapter_doc_assignments_ref"),
        "assign_stats": result.get("assign_stats") or {},
    }
