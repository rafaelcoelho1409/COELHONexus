"""chapter_assign graph node: runs the scoring subgraph (one scorer per page, in parallel, then a sweep over the pages that fell back), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import params, service
from ..... import graphs

import langgraph.graph
import langgraph.runtime
import langgraph.types


@domains.dd.pipeline.observability.service.traced("chapter_assign")
async def chapter_assign(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.chapter_assign.graphs.chapter_assign_graph().ainvoke(
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


async def prepare(
    state: graphs.pipeline.planner.chapter_assign.states.AssignState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.prepare(state, runtime)


async def score_page(
    state: graphs.pipeline.planner.chapter_assign.entities.ScoreInput,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.score_page(state, runtime)


def tally(
    state: graphs.pipeline.planner.chapter_assign.states.AssignState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return service.tally(state, runtime)


async def finalize(
    state: graphs.pipeline.planner.chapter_assign.states.AssignState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.finalize(state, runtime)


def score_input(
    graph_state: graphs.pipeline.planner.chapter_assign.states.AssignState,
    key: str,
    round_: int,
    total: int,
) -> graphs.pipeline.planner.chapter_assign.entities.ScoreInput:
    return {
        "key": key,
        "digest": (graph_state.get("digests") or {})[key],
        "framework_slug": graph_state["framework_slug"],
        "doc_path": (graph_state.get("paths") or {}).get(key, ""),
        "round": round_,
        "total": total,
    }


def after_prepare(graph_state: graphs.pipeline.planner.chapter_assign.states.AssignState):
    if graph_state.get("finished"):
        return langgraph.graph.END
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    return [langgraph.types.Send("score_page", score_input(graph_state, key, 1, len(todo))) for key in todo]


def after_tally(graph_state: graphs.pipeline.planner.chapter_assign.states.AssignState):
    """Pages that ended on the lexical fallback get one more pass, at low concurrency, once the Rotator's pool has recovered."""
    sweep = graph_state.get("sweep") or []
    if sweep:
        return [langgraph.types.Send("score_page", score_input(graph_state, key, 2, len(sweep))) for key in sweep]
    return "finalize"
