"""One chapter of the synth: a LangGraph of seven nodes with a CoRefine loop, one AsyncPostgresSaver checkpoint per node, each stage with LLM work a subgraph of its own.

The node names and the `SynthState` fields are a contract with the web (it reads a node's output field to tell whether it is done, and draws
the loop from `mgsr_replan` back to `sawc_write`), so a stage's inner shape (fan-out workers, validation tools, retry rounds) can change without
the web noticing. Dependencies reach the nodes at run time through the graph's `context` (`SynthContext`: object storage and the run's agents), so
building the graph needs nothing but a checkpointer: the API builds it to read a thread's state."""
from __future__ import annotations
from . import entities, params, states
from .... import nodes

from langgraph.graph import END, START, StateGraph


def synth_graph(checkpointer = None):
    """Compile the chapter graph, checkpointed when a saver is given."""
    if params.is_v2():
        return synth_v2_graph(checkpointer)

    workflow = StateGraph(states.SynthState, context_schema = entities.SynthContext)
    workflow.add_node("outline_sdp", nodes.pipeline.synth.outline.node.outline_sdp)
    workflow.add_node("digest_construct", nodes.pipeline.synth.digest.node.digest_construct)
    workflow.add_node("sawc_write", nodes.pipeline.synth.sawc.node.sawc_write)
    workflow.add_node("sawc_derive", nodes.pipeline.synth.sawc_derive.node.sawc_derive)
    workflow.add_node("checklist_eval", nodes.pipeline.synth.checklist.node.checklist_eval)
    workflow.add_node("mgsr_replan", nodes.pipeline.synth.mgsr.node.mgsr_replan)
    workflow.add_node("render_audit_write", nodes.pipeline.synth.render.node.render_audit_write)
    workflow.add_edge(START, "outline_sdp")
    workflow.add_edge("outline_sdp", "digest_construct")
    workflow.add_edge("digest_construct", "sawc_write")
    workflow.add_edge("sawc_write", "sawc_derive")
    workflow.add_edge("sawc_derive", "checklist_eval")
    workflow.add_edge("checklist_eval", "mgsr_replan")
    workflow.add_conditional_edges(
        "mgsr_replan",
        nodes.pipeline.synth.mgsr.node.route_after_mgsr,
        {"sawc_write": "sawc_write", "render_audit_write": "render_audit_write"})
    workflow.add_edge("render_audit_write", END)
    return workflow.compile(checkpointer = checkpointer, name = "dd_synth")


def synth_v2_graph(checkpointer = None):
    """Compile Synth v2: outline → deterministic code ledger → write → capstone → verify → judge → one revision → render."""
    workflow = StateGraph(states.SynthState, context_schema = entities.SynthContext)
    workflow.add_node("outline_sdp", nodes.pipeline.synth.outline.node.outline_sdp)
    workflow.add_node("ledger_prepare", nodes.pipeline.synth.ledger_prepare.node.ledger_prepare)
    workflow.add_node("sawc_write", nodes.pipeline.synth.sawc.node.sawc_write)
    workflow.add_node("sawc_derive", nodes.pipeline.synth.sawc_derive.node.sawc_derive)
    workflow.add_node("verify", nodes.pipeline.synth.verify.node.verify_code)
    workflow.add_node("checklist_eval", nodes.pipeline.synth.checklist.node.checklist_eval)
    workflow.add_node("mgsr_replan", nodes.pipeline.synth.mgsr.node.mgsr_replan)
    workflow.add_node("render_audit_write", nodes.pipeline.synth.render.node.render_audit_write)
    workflow.add_edge(START, "outline_sdp")
    workflow.add_edge("outline_sdp", "ledger_prepare")
    workflow.add_edge("ledger_prepare", "sawc_write")
    workflow.add_edge("sawc_write", "sawc_derive")
    workflow.add_edge("sawc_derive", "verify")
    workflow.add_edge("verify", "checklist_eval")
    workflow.add_edge("checklist_eval", "mgsr_replan")
    workflow.add_conditional_edges(
        "mgsr_replan",
        nodes.pipeline.synth.mgsr.node.route_after_mgsr_v2,
        {"sawc_write": "sawc_write", "render_audit_write": "render_audit_write"},
    )
    workflow.add_edge("render_audit_write", END)
    return workflow.compile(checkpointer = checkpointer, name = "dd_synth_v2")
