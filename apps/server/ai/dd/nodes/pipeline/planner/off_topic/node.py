"""off_topic graph node: runs the relevance subgraph (one judge per distinct page text, in parallel), and the routes between its steps."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@traced("off_topic")
async def off_topic(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.off_topic.graphs.off_topic_graph().ainvoke(
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


async def prepare(
    state: graphs.pipeline.planner.off_topic.states.OffTopicState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.prepare(state, runtime)


async def judge_page(
    state: graphs.pipeline.planner.off_topic.entities.JudgeInput,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.judge_page(state, runtime)


def tally(
    state: graphs.pipeline.planner.off_topic.states.OffTopicState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return service.tally(state, runtime)


async def finalize(
    state: graphs.pipeline.planner.off_topic.states.OffTopicState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.finalize(state, runtime)


def judge_input(
    graph_state: graphs.pipeline.planner.off_topic.states.OffTopicState,
    digest: str,
    round_: int,
) -> graphs.pipeline.planner.off_topic.entities.JudgeInput:
    groups = graph_state.get("groups") or {}
    n_pages = sum(len(indexes) for indexes in groups.values()) + len(graph_state.get("auto_keep") or [])
    return {
        "digest": digest,
        "key": graph_state["reps"][digest],
        "round": round_,
        "framework_slug": graph_state.get("framework_slug") or "",
        "framework_name": graph_state.get("framework_name") or "",
        "category": graph_state.get("category") or "",
        "summary": graph_state.get("index_summary") or "",
        "total": len(graph_state.get("todo") or []),
        "deduped": n_pages - len(graph_state.get("auto_keep") or []) - len(groups),
    }


def after_prepare(graph_state: graphs.pipeline.planner.off_topic.states.OffTopicState):
    if graph_state.get("finished"):
        return END
    todo = graph_state.get("todo") or []
    if not todo:
        return "tally"
    return [Send("judge_page", judge_input(graph_state, digest, 1)) for digest in todo]


def after_tally(graph_state: graphs.pipeline.planner.off_topic.states.OffTopicState):
    """Confident DROPs of substantial pages are judged a second time (one free-tier judge call is noisy); KEEP wins."""
    recheck = graph_state.get("recheck") or []
    if recheck:
        return [Send("judge_page", judge_input(graph_state, digest, 2)) for digest in recheck]
    return "finalize"
