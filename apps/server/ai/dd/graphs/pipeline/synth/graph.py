"""One chapter of the synth: a LangGraph of seven nodes with a CoRefine loop, one AsyncPostgresSaver checkpoint per node, each stage with LLM work a subgraph of its own.

The node names and the `SynthState` fields are a contract with the web (it reads a node's output field to tell whether it is done, and draws
the loop from `mgsr_replan` back to `sawc_write`), so a stage's inner shape (fan-out workers, validation tools, retry rounds) can change without
the web noticing. Dependencies reach the nodes at run time through the graph's `context` (`SynthContext`: object storage and the run's agents), so
building the graph needs nothing but a checkpointer: the API builds it to read a thread's state."""
from __future__ import annotations
from . import context, router, state
from .... import nodes

from langgraph.graph import END, START, StateGraph


# Canonical order of the nodes. corpus_normalize and the code vaults are ingestion-time work; the per-stage MinIO caches are inside each node.
NODE_ORDER = (
    "outline_sdp",
    "digest_construct",
    "sawc_write",
    "sawc_derive",
    "checklist_eval",
    "mgsr_replan",
    "render_audit_write",
)
IMPLEMENTED = NODE_ORDER

# Primary output field per node: how the web (and a resume) tells that a node has produced its result.
NODE_TO_FIELD = {
    "outline_sdp":        "outline_path",
    "digest_construct":   "digest_path",
    "sawc_write":         "sawc_path",
    "sawc_derive":        "derive_stats",
    "checklist_eval":     "checklist_path",
    "mgsr_replan":        "mgsr_path",
    "render_audit_write": "chapter_path",
}

# The package of `nodes.pipeline.synth` that holds each node's function.
_PACKAGES = {
    "outline_sdp":        "outline",
    "digest_construct":   "digest",
    "sawc_write":         "sawc",
    "sawc_derive":        "sawc_derive",
    "checklist_eval":     "checklist",
    "mgsr_replan":        "mgsr",
    "render_audit_write": "render",
}


def build_synth_graph(checkpointer = None):
    """Compile the chapter graph, checkpointed when a saver is given."""
    stages = nodes.pipeline.synth
    workflow = StateGraph(state.SynthState, context_schema = context.SynthContext)
    for name in NODE_ORDER:
        workflow.add_node(name, getattr(getattr(stages, _PACKAGES[name]).node, name))
    workflow.add_edge(START, NODE_ORDER[0])
    for current, following in zip(NODE_ORDER, NODE_ORDER[1:]):
        if current == "mgsr_replan":
            continue          # conditional: render the chapter, or write it again
        workflow.add_edge(current, following)
    workflow.add_conditional_edges("mgsr_replan", router.route_after_mgsr, {"sawc_write": "sawc_write", "render_audit_write": "render_audit_write"})
    workflow.add_edge(NODE_ORDER[-1], END)
    return workflow.compile(checkpointer = checkpointer, name = "dd_synth")
