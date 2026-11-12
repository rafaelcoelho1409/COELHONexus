"""book_harmonize subgraph: a claim_extractor per chapter → the term_canonicalizer → a drift_detector per chapter (verifying conflicts in the sibling chapters' text) →
a paragraph_patcher per flagged paragraph → finalize (accept an edit only when the deterministic checks do)."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def book_harmonize_graph():
    workflow = StateGraph(states.HarmonizeState)
    workflow.add_node("prepare", nodes.pipeline.synth.book_harmonize.node.prepare)
    workflow.add_node("extract", nodes.pipeline.synth.book_harmonize.node.extract)
    workflow.add_node("canonicalize", nodes.pipeline.synth.book_harmonize.node.canonicalize)
    workflow.add_node("detect", nodes.pipeline.synth.book_harmonize.node.detect)
    workflow.add_node("plan_patches", nodes.pipeline.synth.book_harmonize.node.plan_patches)
    workflow.add_node("patch", nodes.pipeline.synth.book_harmonize.node.patch)
    workflow.add_node("finalize", nodes.pipeline.synth.book_harmonize.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.synth.book_harmonize.node.after_prepare, 
        ["extract", END])
    workflow.add_edge("extract", "canonicalize")
    workflow.add_conditional_edges(
        "canonicalize", 
        nodes.pipeline.synth.book_harmonize.node.after_canonicalize, 
        ["detect", END])
    workflow.add_edge("detect", "plan_patches")
    workflow.add_conditional_edges(
        "plan_patches", 
        nodes.pipeline.synth.book_harmonize.node.after_plan, 
        ["patch", "finalize"])
    workflow.add_edge("patch", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
