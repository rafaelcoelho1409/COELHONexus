"""The planner: a strictly sequential LangGraph of eight nodes, one AsyncPostgresSaver checkpoint per node, each stage with LLM work a subgraph of its own.

The node names and the `PlannerState` fields are a contract with the web (it reads a node's output field to tell whether it is done),
so a stage's inner shape (fan-out workers, score gates, retry rounds) can change without the web noticing. Dependencies reach the
nodes at run time through the graph's `context` (`PlannerContext`: object storage and the run's agents), so building the graph needs
nothing but a checkpointer: the API builds it to read a thread's state."""
from __future__ import annotations
from . import context, state
from .... import nodes

from langgraph.graph import END, START, StateGraph


# Canonical order of the stages.
NODE_ORDER = (
    "corpus_load",
    "off_topic",
    "doc_distill",
    "chapter_propose",
    "chapter_assign",
    "chapter_select",
    "order_chapters",
    "plan_write",
)
IMPLEMENTED = NODE_ORDER

# Primary output field per node: how the web (and a resume) tells that a node has produced its result.
NODE_TO_FIELD = {
    "corpus_load":      "raw_files",
    "off_topic":        "relevant_files",
    "doc_distill":      "doc_distill_ref",
    "chapter_propose":  "chapter_proposals_ref",
    "chapter_assign":   "chapter_doc_assignments_ref",
    "chapter_select":   "chapter_plan_ref",
    "order_chapters":   "chapter_order_ref",
    "plan_write":       "plan_path",
}


def build_planner_graph(checkpointer = None):
    """Compile the planner graph, checkpointed when a saver is given."""
    stages = nodes.pipeline.planner
    registry = {name: getattr(stages, name).node for name in NODE_ORDER}
    workflow = StateGraph(state.PlannerState, context_schema = context.PlannerContext)
    for name in NODE_ORDER:
        workflow.add_node(name, getattr(registry[name], name))
    workflow.add_edge(START, NODE_ORDER[0])
    for current, following in zip(NODE_ORDER, NODE_ORDER[1:]):
        workflow.add_edge(current, following)
    workflow.add_edge(NODE_ORDER[-1], END)
    return workflow.compile(checkpointer = checkpointer, name = "dd_planner")
