"""chapter_select subgraph: prepare (the deterministic selection) → a curator per oversize chapter in parallel → apply → (more rounds) → finalize."""
from __future__ import annotations
from . import states
from ..... import nodes

import langgraph.graph


def chapter_select_graph():
    workflow = langgraph.graph.StateGraph(states.SelectState)
    workflow.add_node("prepare", nodes.pipeline.planner.chapter_select.node.prepare)
    workflow.add_node("curate_chapter", nodes.pipeline.planner.chapter_select.node.curate_chapter)
    workflow.add_node("apply", nodes.pipeline.planner.chapter_select.node.apply)
    workflow.add_node("finalize", nodes.pipeline.planner.chapter_select.node.finalize)
    workflow.add_edge(langgraph.graph.START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.planner.chapter_select.node.after_prepare, 
        ["curate_chapter", "finalize", langgraph.graph.END])
    workflow.add_edge("curate_chapter", "apply")
    workflow.add_conditional_edges(
        "apply", 
        nodes.pipeline.planner.chapter_select.node.after_apply, 
        ["curate_chapter", "finalize"])
    workflow.add_edge("finalize", langgraph.graph.END)
    return workflow.compile()
