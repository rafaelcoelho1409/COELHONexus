"""off_topic subgraph: prepare (dedupe, cache) → one judge per distinct page text in parallel → tally → a second look at big drops → finalize."""
from __future__ import annotations
from . import states
from ..... import nodes

import langgraph.graph


def off_topic_graph():
    workflow = langgraph.graph.StateGraph(states.OffTopicState)
    workflow.add_node("prepare", nodes.pipeline.planner.off_topic.node.prepare)
    workflow.add_node("judge_page", nodes.pipeline.planner.off_topic.node.judge_page)
    workflow.add_node("tally", nodes.pipeline.planner.off_topic.node.tally)
    workflow.add_node("finalize", nodes.pipeline.planner.off_topic.node.finalize)
    workflow.add_edge(langgraph.graph.START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.planner.off_topic.node.after_prepare, 
        ["judge_page", "tally", langgraph.graph.END])
    workflow.add_edge("judge_page", "tally")
    workflow.add_conditional_edges(
        "tally", 
        nodes.pipeline.planner.off_topic.node.after_tally, 
        ["judge_page", "finalize"])
    workflow.add_edge("finalize", langgraph.graph.END)
    return workflow.compile()
