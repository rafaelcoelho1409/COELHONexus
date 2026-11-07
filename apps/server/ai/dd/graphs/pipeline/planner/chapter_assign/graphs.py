"""chapter_assign subgraph: prepare → one scorer per page in parallel → tally → a sweep over the pages that fell back → finalize."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def chapter_assign_graph():
    workflow = StateGraph(states.AssignState)
    workflow.add_node("prepare", nodes.pipeline.planner.chapter_assign.node.prepare)
    workflow.add_node("score_page", nodes.pipeline.planner.chapter_assign.node.score_page)
    workflow.add_node("tally", nodes.pipeline.planner.chapter_assign.node.tally)
    workflow.add_node("finalize", nodes.pipeline.planner.chapter_assign.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.planner.chapter_assign.node.after_prepare, 
        ["score_page", "finalize", END])
    workflow.add_edge("score_page", "tally")
    workflow.add_conditional_edges(
        "tally", 
        nodes.pipeline.planner.chapter_assign.node.after_tally, 
        ["score_page", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
