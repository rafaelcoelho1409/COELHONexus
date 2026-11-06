"""chapter_select subgraph: prepare (the deterministic selection) → a curator per oversize chapter in parallel → apply → (more rounds) → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def _curate_input(graph_state: state.SelectState, item: state.SplitPlanItem) -> dict:
    return {
        "pos": item["pos"],
        "n_parts": item["n_parts"],
        "round": graph_state.get("round") or 1,
        "framework_slug": graph_state["framework_slug"],
    }


def after_prepare(graph_state: state.SelectState):
    if graph_state.get("finished"):
        return END
    plans = graph_state.get("plans") or []
    if not plans:
        return "finalize"
    return [Send("curate_chapter", _curate_input(graph_state, item)) for item in plans]


def after_apply(graph_state: state.SelectState):
    """The parts of a split can themselves be over the code-block cap: another round, up to SPLIT_MAX_ROUNDS."""
    plans = graph_state.get("plans") or []
    if plans:
        return [Send("curate_chapter", _curate_input(graph_state, item)) for item in plans]
    return "finalize"


def build():
    steps = nodes.pipeline.planner.chapter_select.service
    workflow = StateGraph(state.SelectState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("curate_chapter", steps.curate_chapter)
    workflow.add_node("apply", steps.apply)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["curate_chapter", "finalize", END])
    workflow.add_edge("curate_chapter", "apply")
    workflow.add_conditional_edges("apply", after_apply, ["curate_chapter", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
