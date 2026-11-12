"""sawc subgraph: prepare → per stage of the outline's DAG one section_writer per section in parallel (memory flows between stages) → one late retry for
the sections that lost every run → coverage top-up rounds → source top-up → finalize."""
from __future__ import annotations
from . import states
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def sawc_graph():
    workflow = StateGraph(states.SawcState)
    workflow.add_node("prepare", nodes.pipeline.synth.sawc.node.prepare)
    workflow.add_node("start_stage", nodes.pipeline.synth.sawc.node.start_stage)
    workflow.add_node("write_section", nodes.pipeline.synth.sawc.node.write_section)
    workflow.add_node("finish_stage", nodes.pipeline.synth.sawc.node.finish_stage)
    workflow.add_node("plan_coverage", nodes.pipeline.synth.sawc.node.plan_coverage)
    workflow.add_node("extend_coverage", nodes.pipeline.synth.sawc.node.extend_coverage)
    workflow.add_node("merge_coverage", nodes.pipeline.synth.sawc.node.merge_coverage)
    workflow.add_node("plan_sources", nodes.pipeline.synth.sawc.node.plan_sources)
    workflow.add_node("extend_sources", nodes.pipeline.synth.sawc.node.extend_sources)
    workflow.add_node("finalize", nodes.pipeline.synth.sawc.node.finalize)
    workflow.add_edge(START, "prepare")
    workflow.add_conditional_edges(
        "prepare", 
        nodes.pipeline.synth.sawc.node.after_prepare, 
        ["start_stage", END])
    workflow.add_conditional_edges(
        "start_stage", 
        nodes.pipeline.synth.sawc.node.write_fan_out, 
        ["write_section", "finish_stage"])
    workflow.add_edge("write_section", "finish_stage")
    workflow.add_conditional_edges(
        "finish_stage", 
        nodes.pipeline.synth.sawc.node.after_finish, 
        ["start_stage", "plan_coverage"])
    workflow.add_conditional_edges(
        "plan_coverage",
        nodes.pipeline.synth.sawc.node.coverage_fan_out,
        ["extend_coverage", "merge_coverage", "plan_sources"])
    workflow.add_edge("extend_coverage", "merge_coverage")
    workflow.add_edge("merge_coverage", "plan_coverage")
    workflow.add_conditional_edges(
        "plan_sources", 
        nodes.pipeline.synth.sawc.node.sources_fan_out, 
        ["extend_sources", "finalize"])
    workflow.add_edge("extend_sources", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()
