"""sawc subgraph: prepare → per stage of the outline's DAG one section_writer per section in parallel (memory flows between stages) → one late retry for
the sections that lost every run → coverage top-up rounds → source top-up → finalize."""
from __future__ import annotations
from . import state
from ..... import nodes

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


def after_prepare(graph_state: state.SawcState) -> str:
    return END if graph_state.get("finished") else "start_stage"


def write_fan_out(graph_state: state.SawcState):
    """One `write_section` branch per section the stage has to write; none (a stage fully reused) → finish_stage."""
    todo = graph_state.get("todo") or []
    if not todo:
        return "finish_stage"
    return [Send("write_section", {"task": task, "stage_pos": graph_state.get("stage_pos", 0)}) for task in todo]


def after_finish(graph_state: state.SawcState) -> str:
    """After the late retry: top-ups. After a stage: the next stage, then the late retry."""
    return "plan_coverage" if graph_state.get("phase") == "late" else "start_stage"


def coverage_fan_out(graph_state: state.SawcState):
    """One `extend_coverage` branch per section of this round; all rounds done → the source top-up."""
    if graph_state.get("topup_done"):
        return "plan_sources"
    ids = graph_state.get("extend_todo") or []
    if not ids:
        return "merge_coverage"
    return [Send("extend_coverage", {"sid": sid}) for sid in ids]


def sources_fan_out(graph_state: state.SawcState):
    ids = graph_state.get("extend_todo") or []
    if not ids:
        return "finalize"
    return [Send("extend_sources", {"sid": sid}) for sid in ids]


def build():
    steps = nodes.pipeline.synth.sawc.service
    workflow = StateGraph(state.SawcState)
    for name in (
        "prepare", "start_stage", "write_section", "finish_stage", "plan_coverage", "extend_coverage", "merge_coverage",
        "plan_sources", "extend_sources", "finalize",
    ):
        workflow.add_node(name, getattr(steps, name))
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges("prepare", after_prepare, ["start_stage", END])
    workflow.add_conditional_edges("start_stage", write_fan_out, ["write_section", "finish_stage"])
    workflow.add_edge("write_section", "finish_stage")
    workflow.add_conditional_edges("finish_stage", after_finish, ["start_stage", "plan_coverage"])
    workflow.add_conditional_edges("plan_coverage", coverage_fan_out, ["extend_coverage", "merge_coverage", "plan_sources"])
    workflow.add_edge("extend_coverage", "merge_coverage")
    workflow.add_edge("merge_coverage", "plan_coverage")
    workflow.add_conditional_edges("plan_sources", sources_fan_out, ["extend_sources", "finalize"])
    workflow.add_edge("extend_sources", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
