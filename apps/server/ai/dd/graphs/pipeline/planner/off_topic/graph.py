"""off_topic subgraph: prepare (dedupe, cache) → one judge per distinct page text in parallel → tally → a second look at big drops → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def _judge_input(graph_state: state.OffTopicState, digest: str, round_: int) -> dict:
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


def after_prepare(graph_state: state.OffTopicState):
    if graph_state.get("finished"):
        return END
    todo = graph_state.get("todo") or []
    if not todo:
        return "tally"
    return [Send("judge_page", _judge_input(graph_state, digest, 1)) for digest in todo]


def after_tally(graph_state: state.OffTopicState):
    """Confident DROPs of substantial pages are judged a second time (one free-tier judge call is noisy); KEEP wins."""
    recheck = graph_state.get("recheck") or []
    if recheck:
        return [Send("judge_page", _judge_input(graph_state, digest, 2)) for digest in recheck]
    return "finalize"


def build():
    steps = nodes.pipeline.planner.off_topic.service
    workflow = StateGraph(state.OffTopicState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("judge_page", steps.judge_page)
    workflow.add_node("tally", steps.tally)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["judge_page", "tally", END])
    workflow.add_edge("judge_page", "tally")
    workflow.add_conditional_edges("tally", after_tally, ["judge_page", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
