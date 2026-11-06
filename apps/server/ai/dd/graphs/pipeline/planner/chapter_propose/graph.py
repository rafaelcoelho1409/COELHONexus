"""chapter_propose subgraph: prepare → one section analyst per section in parallel → the architect → a score gate that sends it back once → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def after_prepare(graph_state: state.ProposeState):
    """A corpus of enough pages is read by one analyst per section; a small one goes straight to the architect."""
    if graph_state.get("finished"):
        return END
    shards = graph_state.get("shards") or []
    if not shards:
        return "architect"
    target_for = nodes.pipeline.planner.chapter_propose.service.section_target
    return [
        Send(
            "analyze_section",
            {
                "section": shard["label"],
                "keys": shard["keys"],
                "target": target_for(len(shard["keys"])),
                "framework_slug": graph_state["framework_slug"],
            },
        )
        for shard in shards
    ]


def after_architect(graph_state: state.ProposeState):
    """The score gate: an outline below the bar (or none) sends the architect back, with the pages it left uncovered, until
    ARCHITECT_ROUNDS attempts are made; the best outline seen is kept."""
    params = nodes.pipeline.planner.chapter_propose.params
    attempt = graph_state.get("attempt") or 0
    outlines = [o for o in graph_state.get("outlines") or [] if o["attempt"] == attempt]
    if attempt >= params.ARCHITECT_ROUNDS:
        return "finalize"
    if outlines and not nodes.pipeline.planner.chapter_propose.domain.is_weak(outlines[-1]["scored"]):
        return "finalize"
    return "architect"


def build():
    steps = nodes.pipeline.planner.chapter_propose.service
    workflow = StateGraph(state.ProposeState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("analyze_section", steps.analyze_section)
    workflow.add_node("architect", steps.architect)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["analyze_section", "architect", END])
    workflow.add_edge("analyze_section", "architect")
    workflow.add_conditional_edges("architect", after_architect, ["architect", "finalize"])
    workflow.add_edge("finalize", END)
    return workflow.compile()
