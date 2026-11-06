"""book_harmonize subgraph: a claim_extractor per chapter → the term_canonicalizer → a drift_detector per chapter (verifying conflicts in the sibling chapters' text) →
a paragraph_patcher per flagged paragraph → finalize (accept an edit only when the deterministic checks do)."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def after_prepare(graph_state: state.HarmonizeState):
    if graph_state.get("finished"):
        return END
    return [Send("extract", {"chapter_id": chapter_id}) for chapter_id in graph_state["chapter_ids"]]


def after_canonicalize(graph_state: state.HarmonizeState):
    if graph_state.get("finished"):
        return END
    return [Send("detect", {"chapter_id": chapter_id}) for chapter_id in graph_state["chapter_ids"]]


def after_plan(graph_state: state.HarmonizeState):
    todo = graph_state.get("patch_todo") or []
    if not todo:
        return "finalize"
    return [Send("patch", {"task": task}) for task in todo]


def build():
    steps = nodes.pipeline.synth.book_harmonize.service
    workflow = StateGraph(state.HarmonizeState)
    for name in ("prepare", "extract", "canonicalize", "detect", "plan_patches", "patch", "finalize"):
        workflow.add_node(name, getattr(steps, name))
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["extract", END])
    workflow.add_edge("extract", "canonicalize")
    workflow.add_conditional_edges("canonicalize", after_canonicalize, ["detect", END])
    workflow.add_edge("detect", "plan_patches")
    workflow.add_conditional_edges("plan_patches", after_plan, ["patch", "finalize"])
    workflow.add_edge("patch", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
