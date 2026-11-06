"""checklist subgraph: prepare (the deterministic gates) → the chapter_judge agent → two more judge runs in parallel when the first verdict alone would send the
chapter back to the writer → per-criterion majority → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def after_prepare(graph_state: state.ChecklistState) -> str:
    return END if graph_state.get("finished") else "judge"


def after_judge(graph_state: state.ChecklistState):
    """A judge that failed (almost) every criterion, or whose verdict alone sends the chapter back for a rewrite, is checked by more runs."""
    domain = nodes.pipeline.synth.checklist.domain
    params = nodes.pipeline.synth.checklist.params
    run = graph_state.get("judge") or {}
    results = [domain.schemas.CriterionResult.model_validate(r) for r in run.get("results") or []]
    pre = graph_state.get("pre_results") or []
    if run.get("answered") and domain.wants_second_opinions(results, pre_passed = sum(1 for r in pre if r["passed"]), pre_total = len(pre)):
        return [Send("judge_again", {"run": n}) for n in range(1, params.JUDGE_SECOND_OPINIONS + 1)]
    return "finalize"


def build():
    steps = nodes.pipeline.synth.checklist.service
    workflow = StateGraph(state.ChecklistState)
    workflow.add_node("prepare", steps.prepare)
    workflow.add_node("judge", steps.judge)
    workflow.add_node("judge_again", steps.judge_again)
    workflow.add_node("majority", steps.majority)
    workflow.add_node("finalize", steps.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["judge", END])
    workflow.add_conditional_edges("judge", after_judge, ["judge_again", "finalize"])
    workflow.add_edge("judge_again", "majority")
    workflow.add_edge("majority", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
