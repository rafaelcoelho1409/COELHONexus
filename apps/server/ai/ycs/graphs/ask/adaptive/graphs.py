"""Compile the adaptive YCS Ask workflow."""
from __future__ import annotations
import domains
from . import states
from .... import nodes

from functools import partial

from langgraph.graph import END, StateGraph


def adaptive_rag_graph(
    retriever,
    grader: domains.ycs.ask.grader.service.DocumentGrader,
    llm,
    checkpointer = None,
    neo4j_graph = None,
    llm_fast = None,
):
    """Compile adaptive routing across conversation, standard, and deep modes.

    The checkpointer is forwarded to channel-scoped standard subgraphs;
    the adaptive parent graph remains uncheckpointed.
    """
    workflow = StateGraph(states.AdaptiveAskState)
    workflow.add_node(
        "prepare",
        partial(
            nodes.ask.prepare.node.prepare, 
            llm = llm, 
            neo4j_graph = neo4j_graph),
    )
    workflow.add_node(
        "direct_answer",
        partial(
            nodes.ask.direct_answer.node.direct_answer, 
            llm = llm, 
            llm_fast = llm_fast),
    )
    workflow.add_node(
        "run_standard",
        partial(
            nodes.ask.run_standard.node.run_standard_pipeline,
            retriever = retriever,
            grader = grader,
            llm = llm,
            checkpointer = checkpointer,
        ),
    )
    workflow.add_node(
        "plan_research",
        partial(
            nodes.ask.plan.node.plan_research, 
            llm = llm),
    )
    workflow.add_node(
        "run_subagents",
        partial(
            nodes.ask.subagent.node.run_subagents,
            retriever = retriever,
            grader = grader,
            llm = llm,
            checkpointer = checkpointer,
        ),
    )
    workflow.add_node(
        "synthesize", 
        partial(
            nodes.ask.synthesize.node.synthesize, 
            llm = llm))
    workflow.add_node(
        "critic", 
        partial(
            nodes.ask.critic.node.critic, 
            llm = llm))
    workflow.set_entry_point("prepare")
    workflow.add_conditional_edges(
        "prepare",
        nodes.ask.prepare.node.route_by_mode,
        {
            "direct_answer": "direct_answer",
            "run_standard": "run_standard",
            "plan_research": "plan_research",
        },
    )
    workflow.add_conditional_edges(
        "direct_answer",
        nodes.ask.prepare.node.route_after_direct,
        {"end": END, "run_standard": "run_standard"},
    )
    workflow.add_edge("run_standard", END)
    workflow.add_edge("plan_research", "run_subagents")
    workflow.add_edge("run_subagents", "synthesize")
    workflow.add_edge("synthesize", "critic")
    workflow.add_edge("critic", END)
    return workflow.compile()
