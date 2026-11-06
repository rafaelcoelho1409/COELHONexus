"""doc_distill subgraph: prepare (node blob, per-page cache) → one distiller per page in parallel → finalize (the node's blob)."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def after_prepare(graph_state: state.DistillState):
    if graph_state.get("finished"):
        return END
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    digests = graph_state.get("digests") or {}
    return [
        Send(
            "distill_page",
            {"key": key, "digest": digests[key], "framework_slug": graph_state["framework_slug"], "total": len(todo)},
        )
        for key in todo
    ]


def build():
    steps = nodes.pipeline.planner.doc_distill.service
    workflow = StateGraph(state.DistillState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("distill_page", steps.distill_page)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["distill_page", "finalize", END])
    workflow.add_edge("distill_page", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
