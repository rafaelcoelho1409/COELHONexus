"""doc_distill subgraph: prepare (node blob, per-page cache) → one distiller per page in parallel → finalize (the node's blob)."""
from __future__ import annotations
from . import states
from ..... import nodes

import langgraph.graph


def doc_distill_graph():
    workflow = langgraph.graph.StateGraph(states.DistillState)
    workflow.add_node("prepare", nodes.pipeline.planner.doc_distill.node.prepare)
    workflow.add_node("distill_page", nodes.pipeline.planner.doc_distill.node.distill_page)
    workflow.add_node("finalize", nodes.pipeline.planner.doc_distill.node.finalize)
    workflow.add_edge(langgraph.graph.START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.planner.doc_distill.node.after_prepare, 
        ["distill_page", "finalize", langgraph.graph.END])
    workflow.add_edge("distill_page", "finalize")
    workflow.add_edge("finalize", langgraph.graph.END)
    return workflow.compile()
