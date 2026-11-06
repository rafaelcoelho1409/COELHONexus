"""order_chapters graph node: runs the ordering subgraph (N sequencer samples in parallel, then the Borda aggregate and the pins)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced("order_chapters")
async def order_chapters(
    state: graphs.pipeline.planner.state.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.context.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.order_chapters.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "chapter_plan_ref": state.get("chapter_plan_ref") or "",
        },
        {"max_concurrency": params.SAMPLE_CONCURRENCY},
        context = runtime.context,
    )
    return {
        "chapter_order_ref": result.get("chapter_order_ref") or "",
        "order_chapters_stats": result.get("order_chapters_stats") or {},
    }
