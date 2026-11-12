"""doc_distill graph node: runs the distillation subgraph (one distiller per page, in parallel), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import params, service
from ..... import graphs

import langgraph.graph
import langgraph.runtime
import langgraph.types


@domains.dd.pipeline.observability.service.traced("doc_distill")
async def doc_distill(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.doc_distill.graphs.doc_distill_graph().ainvoke(
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


async def prepare(
    state: graphs.pipeline.planner.doc_distill.states.DistillState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.prepare(state, runtime)


async def distill_page(
    state: graphs.pipeline.planner.doc_distill.entities.DistillInput,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.distill_page(state, runtime)


async def finalize(
    state: graphs.pipeline.planner.doc_distill.states.DistillState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.planner.doc_distill.states.DistillState):
    if graph_state.get("finished"):
        return langgraph.graph.END
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    digests = graph_state.get("digests") or {}
    return [
        langgraph.types.Send(
            "distill_page",
            {"key": key, "digest": digests[key], "framework_slug": graph_state["framework_slug"], "total": len(todo)},
        )
        for key in todo
    ]
