"""checklist subgraph: prepare (the deterministic gates) → the chapter_judge agent → two more judge runs in parallel when the first verdict alone would send the
chapter back to the writer → per-criterion majority → finalize."""
from __future__ import annotations
from . import states
from ..... import nodes

import langgraph.graph


def checklist_graph():
    workflow = langgraph.graph.StateGraph(states.ChecklistState)
    workflow.add_node("prepare", nodes.pipeline.synth.checklist.node.prepare)
    workflow.add_node("judge", nodes.pipeline.synth.checklist.node.judge)
    workflow.add_node("judge_again", nodes.pipeline.synth.checklist.node.judge_again)
    workflow.add_node("majority", nodes.pipeline.synth.checklist.node.majority)
    workflow.add_node("finalize", nodes.pipeline.synth.checklist.node.finalize)
    workflow.add_edge(langgraph.graph.START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.synth.checklist.node.after_prepare, 
        ["judge", langgraph.graph.END])
    workflow.add_conditional_edges(
        "judge", 
        nodes.pipeline.synth.checklist.node.after_judge, 
        ["judge_again", "finalize"])
    workflow.add_edge("judge_again", "majority")
    workflow.add_edge("majority", "finalize")
    workflow.add_edge("finalize", langgraph.graph.END)
    return workflow.compile()
