"""Compile the adaptive YCS Ask workflow from visible LangGraph nodes."""
from __future__ import annotations
import domains
from . import states
from .... import nodes

from functools import partial

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph


def adaptive_rag_graph(
    retriever,
    grader: domains.ycs.ask.grader.service.DocumentGrader,
    llm,
    checkpointer = None,
    neo4j_graph = None,
    llm_fast = None,
):
    """Compile conversation, standard video RAG, and deep research routes.

    Standard RAG nodes are wired directly into the adaptive parent graph
    instead of being hidden behind a single `run_standard` invocation.
    This keeps the live graph and persisted execution trace aligned with
    the YCS retrieve → grade → rewrite/generate → verify/cite pipeline.
    """
    workflow = StateGraph(states.AdaptiveAskState)
    workflow.add_node(
        "prepare",
        partial(
            nodes.ask.prepare.node.prepare,
            llm = llm,
            neo4j_graph = neo4j_graph,
        ),
    )
    workflow.add_node(
        "direct_answer",
        partial(
            nodes.ask.direct_answer.node.direct_answer,
            llm = llm,
            llm_fast = llm_fast,
        ),
    )
    workflow.add_node("retrieve", _retrieve_node(retriever))
    workflow.add_node("grade_documents", _grade_node(grader))
    workflow.add_node("rewrite_query", _rewrite_node(llm))
    workflow.add_node("generate", _generate_node(llm))
    workflow.add_node("check_hallucination", _hallucination_node(llm))
    workflow.add_node("fallback_answer", _fallback_node(llm))
    workflow.add_node("corroborate", _corroborate_node(llm))
    workflow.add_node("format_citations", _citation_node())
    workflow.add_node(
        "plan_research",
        partial(nodes.ask.plan.node.plan_research, llm = llm),
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
        partial(nodes.ask.synthesize.node.synthesize, llm = llm),
    )
    workflow.add_node(
        "critic",
        partial(nodes.ask.critic.node.critic, llm = llm),
    )

    workflow.set_entry_point("prepare")
    workflow.add_conditional_edges(
        "prepare",
        nodes.ask.prepare.node.route_by_mode,
        {
            "direct_answer": "direct_answer",
            "retrieve": "retrieve",
            "plan_research": "plan_research",
        },
    )
    workflow.add_conditional_edges(
        "direct_answer",
        nodes.ask.prepare.node.route_after_direct,
        {"end": END, "retrieve": "retrieve"},
    )
    workflow.add_edge("retrieve", "grade_documents")
    workflow.add_conditional_edges(
        "grade_documents",
        _route_after_grade,
        {
            "generate": "generate",
            "rewrite": "rewrite_query",
            "fallback": "fallback_answer",
        },
    )
    workflow.add_edge("rewrite_query", "retrieve")
    workflow.add_edge("generate", "check_hallucination")
    workflow.add_conditional_edges(
        "check_hallucination",
        _route_after_hallucination_check,
        {
            "format_citations": "format_citations",
            "rewrite": "rewrite_query",
            "corroborate": "corroborate",
        },
    )
    workflow.add_edge("fallback_answer", END)
    workflow.add_edge("corroborate", "format_citations")
    workflow.add_edge("format_citations", END)
    workflow.add_edge("plan_research", "run_subagents")
    workflow.add_edge("run_subagents", "synthesize")
    workflow.add_edge("synthesize", "critic")
    workflow.add_edge("critic", END)
    return workflow.compile()


def _retrieve_node(retriever):
    async def retrieve(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.retrieve.node.retrieve(
            graph_state,
            retriever,
            graph_state.get("channel_ids") or None,
        )

    return retrieve


def _grade_node(grader):
    async def grade(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.grade.node.grade_documents(
            graph_state,
            grader,
        )

    return grade


def _rewrite_node(llm):
    async def rewrite(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.rewrite.node.rewrite_query(
            graph_state,
            llm,
        )

    return rewrite


def _generate_node(llm):
    async def generate(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.generate.node.generate(
            graph_state,
            llm,
        )

    return generate


def _hallucination_node(llm):
    async def check_hallucination(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.hallucination.node.check_hallucination(
            graph_state,
            llm,
        )

    return check_hallucination


def _fallback_node(llm):
    async def fallback_answer(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.fallback_answer.node.fallback_answer(
            graph_state,
            llm,
        )

    return fallback_answer


def _corroborate_node(llm):
    async def corroborate(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.corroborate.node.corroborate_claim(
            graph_state,
            llm,
        )

    return corroborate


def _citation_node():
    async def format_citations(graph_state: states.AdaptiveAskState) -> dict:
        return await domains.ycs.ask.rag.standard.nodes.cite.node.format_citations(
            graph_state,
        )

    return format_citations


def _route_after_grade(
    graph_state: states.AdaptiveAskState,
    config: RunnableConfig,
) -> str:
    return domains.ycs.ask.rag.standard.graph.decide_after_grading(
        graph_state,
        config,
    )


def _route_after_hallucination_check(
    graph_state: states.AdaptiveAskState,
    config: RunnableConfig,
) -> str:
    return domains.ycs.ask.rag.standard.graph.decide_after_hallucination_check(
        graph_state,
        config,
    )
