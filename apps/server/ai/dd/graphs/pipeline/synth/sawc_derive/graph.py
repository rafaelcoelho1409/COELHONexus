"""sawc_derive subgraph: prepare (the thin code blocks) → one code_deriver per thin subtopic in parallel → finalize (write the derived code into the chapter draft)."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def after_prepare(graph_state: state.DeriveState):
    if graph_state.get("finished"):
        return END
    count = graph_state.get("n_candidates", 0)
    if not count:
        return "finalize"
    return [Send("derive_subtopic", {"index": index}) for index in range(count)]


def build():
    steps = nodes.pipeline.synth.sawc_derive.service
    workflow = StateGraph(state.DeriveState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("derive_subtopic", steps.derive_subtopic)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["derive_subtopic", "finalize", END])
    workflow.add_edge("derive_subtopic", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
