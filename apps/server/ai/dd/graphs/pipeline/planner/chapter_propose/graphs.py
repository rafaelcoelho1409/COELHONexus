"""chapter_propose subgraph: prepare → one section analyst per section in parallel → the architect → a score gate that sends it back once → finalize."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def chapter_propose_graph():
    workflow = StateGraph(states.ProposeState)
    workflow.add_node("prepare", nodes.pipeline.planner.chapter_propose.node.prepare)
    workflow.add_node("analyze_section", nodes.pipeline.planner.chapter_propose.node.analyze_section)
    workflow.add_node("architect", nodes.pipeline.planner.chapter_propose.node.architect)
    workflow.add_node("finalize", nodes.pipeline.planner.chapter_propose.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.planner.chapter_propose.node.after_prepare, 
        ["analyze_section", "architect", END])
    workflow.add_edge("analyze_section", "architect")
    workflow.add_conditional_edges(
        "architect", 
        nodes.pipeline.planner.chapter_propose.node.after_architect, 
        ["architect", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
