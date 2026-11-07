"""digest subgraph: prepare (outline, sources, per-chunk cache) → one source_router per chunk in parallel → a second wave for the ones that failed → finalize."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def digest_graph():
    workflow = StateGraph(states.DigestState)
    workflow.add_node("prepare", nodes.pipeline.synth.digest.node.prepare)
    workflow.add_node("route_source", nodes.pipeline.synth.digest.node.route_source)
    workflow.add_node("collect", nodes.pipeline.synth.digest.node.collect)
    workflow.add_node("finalize", nodes.pipeline.synth.digest.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.synth.digest.node.after_prepare, 
        ["route_source", "finalize", END])
    workflow.add_edge("route_source", "collect")
    workflow.add_conditional_edges(
        "collect", 
        nodes.pipeline.synth.digest.node.fan_out, 
        ["route_source", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
