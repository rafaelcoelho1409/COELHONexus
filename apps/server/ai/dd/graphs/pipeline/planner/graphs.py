"""The planner: a strictly sequential LangGraph of eight nodes, one AsyncPostgresSaver checkpoint per node, each stage with LLM work a subgraph of its own.

The node names and the `PlannerState` fields are a contract with the web (it reads a node's output field to tell whether it is done),
so a stage's inner shape (fan-out workers, score gates, retry rounds) can change without the web noticing. Dependencies reach the
nodes at run time through the graph's `context` (`PlannerContext`: object storage and the run's agents), so building the graph needs
nothing but a checkpointer: the API builds it to read a thread's state."""
from __future__ import annotations
from . import entities, params, states
from .... import nodes

from langgraph.graph import END, START, StateGraph


def planner_graph(checkpointer = None, planner_v2: bool | None = None):
    """Compile the selected planner graph, checkpointed when a saver is given."""
    use_v2 = params.PLANNER_V2 if planner_v2 is None else planner_v2
    workflow = StateGraph(states.PlannerState, context_schema = entities.PlannerContext)
    workflow.add_node("corpus_load", nodes.pipeline.planner.corpus_load.node.corpus_load)
    if use_v2:
        workflow.add_node("page_cards", nodes.pipeline.planner.page_cards.node.page_cards)
        workflow.add_node("chapter_design", nodes.pipeline.planner.chapter_design.node.chapter_design)
        workflow.add_node("chapter_select", nodes.pipeline.planner.chapter_select.node.chapter_select)
        workflow.add_node("order_chapters", nodes.pipeline.planner.order_chapters.node.order_chapters)
        workflow.add_node("plan_write", nodes.pipeline.planner.plan_write.node.plan_write)
        workflow.add_edge(START, "corpus_load")
        workflow.add_edge("corpus_load", "page_cards")
        workflow.add_edge("page_cards", "chapter_design")
        workflow.add_edge("chapter_design", "chapter_select")
        workflow.add_edge("chapter_select", "order_chapters")
        workflow.add_edge("order_chapters", "plan_write")
        workflow.add_edge("plan_write", END)
        return workflow.compile(checkpointer = checkpointer, name = "dd_planner_v2")

    workflow.add_node("off_topic", nodes.pipeline.planner.off_topic.node.off_topic)
    workflow.add_node("doc_distill", nodes.pipeline.planner.doc_distill.node.doc_distill)
    workflow.add_node("chapter_propose", nodes.pipeline.planner.chapter_propose.node.chapter_propose)
    workflow.add_node("chapter_assign", nodes.pipeline.planner.chapter_assign.node.chapter_assign)
    workflow.add_node("chapter_select", nodes.pipeline.planner.chapter_select.node.chapter_select)
    workflow.add_node("order_chapters", nodes.pipeline.planner.order_chapters.node.order_chapters)
    workflow.add_node("plan_write", nodes.pipeline.planner.plan_write.node.plan_write)
    workflow.add_edge(START, "corpus_load")
    workflow.add_edge("corpus_load", "off_topic")
    workflow.add_edge("off_topic", "doc_distill")
    workflow.add_edge("doc_distill", "chapter_propose")
    workflow.add_edge("chapter_propose", "chapter_assign")
    workflow.add_edge("chapter_assign", "chapter_select")
    workflow.add_edge("chapter_select", "order_chapters")
    workflow.add_edge("order_chapters", "plan_write")
    workflow.add_edge("plan_write", END)
    return workflow.compile(checkpointer = checkpointer, name = "dd_planner")
