"""sawc_derive subgraph: prepare (the thin code blocks) → one code_deriver per thin subtopic in parallel → finalize (write the derived code into the chapter draft) → capstone (tutorial chapters only: the application that ends the chapter)."""
from __future__ import annotations
from . import states
from ..... import nodes

import langgraph.graph


def sawc_derive_graph():
    workflow = langgraph.graph.StateGraph(states.DeriveState)
    workflow.add_node("prepare", nodes.pipeline.synth.sawc_derive.node.prepare)
    workflow.add_node("derive_subtopic", nodes.pipeline.synth.sawc_derive.node.derive_subtopic)
    workflow.add_node("finalize", nodes.pipeline.synth.sawc_derive.node.finalize)
    workflow.add_node("capstone", nodes.pipeline.synth.sawc_derive.node.capstone)
    workflow.add_edge(langgraph.graph.START, "prepare")
    workflow.add_conditional_edges(
        "prepare",
        nodes.pipeline.synth.sawc_derive.node.after_prepare, ["derive_subtopic", "finalize", langgraph.graph.END])
    workflow.add_edge("derive_subtopic", "finalize")
    workflow.add_edge("finalize", "capstone")
    workflow.add_edge("capstone", langgraph.graph.END)
    return workflow.compile()
