"""order_chapters subgraph: prepare → N sequencer samples in parallel → collect (one retry round for a thin batch) → Borda + pins."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def order_chapters_graph():
    workflow = StateGraph(states.OrderState)
    workflow.add_node("prepare", nodes.pipeline.planner.order_chapters.node.prepare)
    workflow.add_node("sample_order", nodes.pipeline.planner.order_chapters.node.sample_order)
    workflow.add_node("collect", nodes.pipeline.planner.order_chapters.node.collect)
    workflow.add_node("finalize", nodes.pipeline.planner.order_chapters.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.planner.order_chapters.node.after_prepare, 
        ["sample_order", END])
    workflow.add_edge("sample_order", "collect")
    workflow.add_conditional_edges(
        "collect", 
        nodes.pipeline.planner.order_chapters.node.after_collect, 
        ["sample_order", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
