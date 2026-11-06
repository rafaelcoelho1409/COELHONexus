"""outline subgraph: prepare (plan, sources, cache) → the outliner agent, again with what it left wrong → finalize (flatten, trim, write)."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def after_prepare(graph_state: state.OutlineState) -> str:
    return END if graph_state.get("finished") else "draft"


def after_draft(graph_state: state.OutlineState) -> str:
    """Another run while the best outline still has issues (or there is none) and the attempts are not spent."""
    params = nodes.pipeline.synth.outline.params
    best = graph_state.get("best")
    issues = (best or {}).get("issues") or []
    attempts = graph_state.get("attempt", 0)
    floor_open = any(nodes.pipeline.synth.outline.domain.is_floor_issue(issue) for issue in issues)
    limit = params.MAX_ATTEMPTS + (params.FLOOR_REPAIR_EXTRA if floor_open else 0)
    if (best is None or issues) and attempts < limit:
        return "draft"
    return "finalize"


def build():
    steps = nodes.pipeline.synth.outline.service
    workflow = StateGraph(state.OutlineState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("draft", steps.draft)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["draft", END])
    workflow.add_conditional_edges("draft", after_draft, ["draft", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
