"""render subgraph: prepare (the draft, the vaults, the code already normalized) → one code_normalizer per distinct code block still to repair → finalize (render, audit, write)."""
from __future__ import annotations
from . import states
from ..... import nodes

import langgraph.graph


def render_graph():
    workflow = langgraph.graph.StateGraph(states.RenderState)
    workflow.add_node("prepare", nodes.pipeline.synth.render.node.prepare)
    workflow.add_node("normalize_block", nodes.pipeline.synth.render.node.normalize_block)
    workflow.add_node("finalize", nodes.pipeline.synth.render.node.finalize)
    workflow.add_edge(langgraph.graph.START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.synth.render.node.after_prepare, 
        ["normalize_block", "finalize", langgraph.graph.END])
    workflow.add_edge("normalize_block", "finalize")
    workflow.add_edge("finalize", langgraph.graph.END)
    return workflow.compile()
