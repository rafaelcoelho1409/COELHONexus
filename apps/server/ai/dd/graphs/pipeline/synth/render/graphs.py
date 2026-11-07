"""render subgraph: prepare (the draft, the vaults, the code already normalized) → one code_normalizer per distinct code block still to repair → finalize (render, audit, write)."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def render_graph():
    workflow = StateGraph(states.RenderState)
    workflow.add_node("prepare", nodes.pipeline.synth.render.node.prepare)
    workflow.add_node("normalize_block", nodes.pipeline.synth.render.node.normalize_block)
    workflow.add_node("finalize", nodes.pipeline.synth.render.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.synth.render.node.after_prepare, 
        ["normalize_block", "finalize", END])
    workflow.add_edge("normalize_block", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
