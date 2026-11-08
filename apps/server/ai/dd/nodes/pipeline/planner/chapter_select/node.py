"""chapter_select graph node: runs the selection subgraph (deterministic selection, then a curator agent per oversize chapter), and the routes between its steps."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@traced("chapter_select")
async def chapter_select(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.chapter_select.graphs.chapter_select_graph().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "chapter_proposals_ref": state.get("chapter_proposals_ref"),
            "chapter_doc_assignments_ref": state.get("chapter_doc_assignments_ref"),
            "planner_version": state.get("planner_version") or "v1",
            "page_cards_ref": state.get("page_cards_ref"),
        },
        {"max_concurrency": params.CURATOR_CONCURRENCY},
        context = runtime.context,
    )
    return {
        "chapter_plan_ref": result.get("chapter_plan_ref"),
        "select_stats": result.get("select_stats") or {},
    }


async def prepare(
    state: graphs.pipeline.planner.chapter_select.states.SelectState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.prepare(state, runtime)


async def curate_chapter(
    state: graphs.pipeline.planner.chapter_select.entities.CurateInput,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.curate_chapter(state, runtime)


def apply(
    state: graphs.pipeline.planner.chapter_select.states.SelectState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return service.apply(state, runtime)


async def finalize(
    state: graphs.pipeline.planner.chapter_select.states.SelectState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.finalize(state, runtime)


def curate_input(
    graph_state: graphs.pipeline.planner.chapter_select.states.SelectState,
    item: graphs.pipeline.planner.chapter_select.entities.SplitPlanItem,
) -> graphs.pipeline.planner.chapter_select.entities.CurateInput:
    return {
        "pos": item["pos"],
        "n_parts": item["n_parts"],
        "round": graph_state.get("round") or 1,
        "framework_slug": graph_state["framework_slug"],
    }


def after_prepare(graph_state: graphs.pipeline.planner.chapter_select.states.SelectState):
    if graph_state.get("finished"):
        return END
    plans = graph_state.get("plans") or []
    if not plans:
        return "finalize"
    return [Send("curate_chapter", curate_input(graph_state, item)) for item in plans]


def after_apply(graph_state: graphs.pipeline.planner.chapter_select.states.SelectState):
    """The parts of a split can themselves be over the code-block cap: another round, up to SPLIT_MAX_ROUNDS."""
    plans = graph_state.get("plans") or []
    if plans:
        return [Send("curate_chapter", curate_input(graph_state, item)) for item in plans]
    return "finalize"
