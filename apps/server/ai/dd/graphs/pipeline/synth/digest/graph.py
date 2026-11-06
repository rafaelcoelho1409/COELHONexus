"""digest subgraph: prepare (outline, sources, per-chunk cache) → one source_router per chunk in parallel → a second wave for the ones that failed → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def fan_out(graph_state: state.DigestState):
    """One `route_source` branch per item still to route; none left → finalize."""
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    return [Send("route_source", {"item": item, "total": len(todo), "wave": graph_state.get("wave", 0)}) for item in todo]


def after_prepare(graph_state: state.DigestState):
    if graph_state.get("finished"):
        return END
    return fan_out(graph_state)


def build():
    steps = nodes.pipeline.synth.digest.service
    workflow = StateGraph(state.DigestState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("route_source", steps.route_source)
    workflow.add_node("collect", steps.collect)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["route_source", "finalize", END])
    workflow.add_edge("route_source", "collect")
    workflow.add_conditional_edges("collect", fan_out, ["route_source", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
