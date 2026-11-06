"""mgsr subgraph: prepare (checklist, outline, cache; a passed chapter is not reviewed) → the chapter_reviewer agent → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def after_prepare(graph_state: state.MgsrState) -> str:
    if graph_state.get("finished"):
        return END
    return "finalize" if graph_state.get("trivial") else "review"


def build():
    steps = nodes.pipeline.synth.mgsr.service
    workflow = StateGraph(state.MgsrState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("review", steps.review)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["review", "finalize", END])
    workflow.add_edge("review", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
