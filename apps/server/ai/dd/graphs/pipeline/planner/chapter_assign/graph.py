"""chapter_assign subgraph: prepare → one scorer per page in parallel → tally → a sweep over the pages that fell back → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def _score_input(graph_state: state.AssignState, key: str, round_: int, total: int) -> dict:
    return {
        "key": key,
        "digest": (graph_state.get("digests") or {})[key],
        "framework_slug": graph_state["framework_slug"],
        "doc_path": (graph_state.get("paths") or {}).get(key, ""),
        "round": round_,
        "total": total,
    }


def after_prepare(graph_state: state.AssignState):
    if graph_state.get("finished"):
        return END
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    return [Send("score_page", _score_input(graph_state, key, 1, len(todo))) for key in todo]


def after_tally(graph_state: state.AssignState):
    """Pages that ended on the lexical fallback get one more pass, at low concurrency, once the Rotator's pool has recovered."""
    sweep = graph_state.get("sweep") or []
    if sweep:
        return [Send("score_page", _score_input(graph_state, key, 2, len(sweep))) for key in sweep]
    return "finalize"


def build():
    steps = nodes.pipeline.planner.chapter_assign.service
    workflow = StateGraph(state.AssignState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("score_page", steps.score_page)
    workflow.add_node("tally", steps.tally)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["score_page", "finalize", END])
    workflow.add_edge("score_page", "tally")
    workflow.add_conditional_edges("tally", after_tally, ["score_page", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
