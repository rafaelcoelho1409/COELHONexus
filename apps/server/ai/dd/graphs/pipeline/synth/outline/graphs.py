"""outline subgraph: prepare (plan, sources, cache) → the outliner agent, again with what it left wrong → finalize (flatten, trim, write)."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def outline_graph():
    workflow = StateGraph(states.OutlineState)
    workflow.add_node("prepare", nodes.pipeline.synth.outline.node.prepare)
    workflow.add_node("draft", nodes.pipeline.synth.outline.node.draft)
    workflow.add_node("finalize", nodes.pipeline.synth.outline.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.synth.outline.node.after_prepare, 
        ["draft", END])
    workflow.add_conditional_edges(
        "draft", 
        nodes.pipeline.synth.outline.node.after_draft, 
        ["draft", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
