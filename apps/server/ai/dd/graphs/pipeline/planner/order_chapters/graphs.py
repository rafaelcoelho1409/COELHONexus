"""order_chapters subgraph: prepare → N sequencer samples in parallel → collect (one retry round for a thin batch) → Borda + pins."""
from __future__ import annotations
from . import states
from ..... import nodes

import langgraph.graph


def order_chapters_graph():
    workflow = langgraph.graph.StateGraph(states.OrderState)
    workflow.add_node("prepare", nodes.pipeline.planner.order_chapters.node.prepare)
    workflow.add_node("sample_order", nodes.pipeline.planner.order_chapters.node.sample_order)
    workflow.add_node("collect", nodes.pipeline.planner.order_chapters.node.collect)
    workflow.add_node("finalize", nodes.pipeline.planner.order_chapters.node.finalize)
    workflow.add_edge(langgraph.graph.START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.planner.order_chapters.node.after_prepare, 
        ["sample_order", langgraph.graph.END])
    workflow.add_edge("sample_order", "collect")
    workflow.add_conditional_edges(
        "collect", 
        nodes.pipeline.planner.order_chapters.node.after_collect, 
        ["sample_order", "finalize"])
    workflow.add_edge("finalize", langgraph.graph.END)
    return workflow.compile()
