"""doc_distill graph node: runs the distillation subgraph (one distiller per page, in parallel)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced("doc_distill")
async def doc_distill(
    state: graphs.pipeline.planner.state.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.context.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.doc_distill.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "raw_files": state.get("raw_files") or [],
            "relevant_files": state.get("relevant_files") or [],
        },
        {"max_concurrency": params.CONCURRENCY},
        context = runtime.context,
    )
    return {
        "doc_distill_ref": result.get("doc_distill_ref"),
        "doc_distill_stats": result.get("doc_distill_stats") or {},
    }
