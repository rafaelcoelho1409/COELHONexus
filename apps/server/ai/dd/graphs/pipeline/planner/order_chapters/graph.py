"""order_chapters subgraph: prepare → N sequencer samples in parallel → collect (one retry round for a thin batch) → Borda + pins."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def _sample_input(graph_state: state.OrderState, sample_idx: int) -> dict:
    return {
        "sample_idx": sample_idx,
        "framework_slug": graph_state["framework_slug"],
        "chapters": graph_state["chapters"],
        "composition": graph_state.get("composition"),
    }


def after_prepare(graph_state: state.OrderState):
    """A node answered from its cache or by a rule ends here; otherwise one sequencer sample per Send."""
    if graph_state.get("finished"):
        return END
    n_samples = nodes.pipeline.planner.order_chapters.params.N_SAMPLES
    return [Send("sample_order", _sample_input(graph_state, i)) for i in range(n_samples)]


def after_collect(graph_state: state.OrderState):
    """A first round with fewer valid samples than MIN_VALID_SAMPLES is topped up once, for the missing indexes only."""
    retry = graph_state.get("retry_samples") or []
    if retry:
        return [Send("sample_order", _sample_input(graph_state, i)) for i in retry]
    return "finalize"


def build():
    steps = nodes.pipeline.planner.order_chapters.service
    workflow = StateGraph(state.OrderState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("sample_order", steps.sample_order)
    workflow.add_node("collect", steps.collect)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["sample_order", END])
    workflow.add_edge("sample_order", "collect")
    workflow.add_conditional_edges("collect", after_collect, ["sample_order", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
