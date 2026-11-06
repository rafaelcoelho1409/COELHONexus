"""render subgraph: prepare (the draft, the vaults, the code already normalized) → one code_normalizer per distinct code block still to repair → finalize (render, audit, write)."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def after_prepare(graph_state: state.RenderState):
    if graph_state.get("finished"):
        return END
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    return [Send("normalize_block", {"hash": h}) for h in todo]


def build():
    steps = nodes.pipeline.synth.render.service
    workflow = StateGraph(state.RenderState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("normalize_block", steps.normalize_block)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["normalize_block", "finalize", END])
    workflow.add_edge("normalize_block", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
