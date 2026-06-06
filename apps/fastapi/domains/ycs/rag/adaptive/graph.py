"""ycs/rag/adaptive — `build_adaptive_rag_graph()` — FAST/STANDARD/DEEP wiring.

Topology (deprecated `graphs/youtube/adaptive.py:L15-29`):

    START
      ↓
    contextualize
      ↓
    classify_query
      ├── FAST     → direct_answer → END
      ├── STANDARD → run_standard  → END
      └── DEEP     → plan_research → Send(run_subagent) ... → synthesize → critic → END

The STANDARD path is the deprecated `YouTubeContentGraph`; both the
`run_standard` and `run_subagent` nodes invoke a channel-scoped
sub-graph built from the parent state's `channel_ids`."""
from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from domains.ycs.grader import DocumentGrader
from domains.ycs.rag.standard import build_youtube_rag_graph

from .nodes.classify import classify_query
from .nodes.contextualize import contextualize_question
from .nodes.critic import critic
from .nodes.direct_answer import direct_answer
from .nodes.plan import plan_research
from .nodes.run_standard import run_standard_pipeline
from .nodes.subagent import run_subagent
from .nodes.synthesize import synthesize
from .state import AdaptiveRAGState


def _route_by_mode(state: AdaptiveRAGState) -> str:
    """Route after classification: FAST, STANDARD, or DEEP."""
    mode = state.get("mode", "standard").lower()
    if mode == "fast":
        return "direct_answer"
    if mode == "deep":
        return "plan_research"
    return "run_standard"


def _fan_out_subagents(state: AdaptiveRAGState) -> list[Send]:
    """After planning, fan out sub-questions to parallel subagents via
    `Send`. Each Send carries one sub-question + the inherited channel
    scope."""
    channel_ids = state.get("channel_ids") or []
    return [
        Send(
            "run_subagent",
            {"sub_question": q, "channel_ids": channel_ids},
        )
        for q in state.get("sub_questions", [])
    ]


def build_adaptive_rag_graph(
    retriever,
    grader: DocumentGrader,
    llm,
    checkpointer = None,
    neo4j_graph = None,
):
    """Build the Adaptive RAG parent graph.

    Wraps the STANDARD pipeline as a sub-graph and adds FAST (direct
    answer) and DEEP (multi-agent research) paths. Channel scope auto-
    detection runs in `classify_query` via `neo4j_graph`.

    `checkpointer` is accepted but unused (preserved for API
    compatibility with deprecated)."""

    def _build_standard_graph(channel_ids: list[str] | None = None):
        """Build a STANDARD pipeline scoped to specific channels."""
        return build_youtube_rag_graph(
            retriever = retriever,
            grader = grader,
            llm = llm,
            checkpointer = checkpointer,
            channel_ids = channel_ids or None,
        )

    workflow = StateGraph(AdaptiveRAGState)

    # Bind deps via async closures — LangGraph requires the node value
    # to be a true async callable.
    async def _contextualize(state):
        return await contextualize_question(state, llm)

    async def _classify(state):
        return await classify_query(state, llm, neo4j_graph)

    async def _direct(state):
        return await direct_answer(state, llm)

    async def _run_standard(state):
        scoped_graph = _build_standard_graph(state.get("channel_ids"))
        return await run_standard_pipeline(state, scoped_graph)

    async def _plan(state):
        return await plan_research(state, llm)

    async def _subagent(payload):
        # Sub-agents inherit the channel scope from the parent state.
        channel_ids = payload.get("channel_ids")
        scoped_graph = _build_standard_graph(channel_ids)
        return await run_subagent(payload, scoped_graph)

    async def _synthesize(state):
        return await synthesize(state, llm)

    async def _critic(state):
        return await critic(state, llm)

    workflow.add_node("contextualize",   _contextualize)
    workflow.add_node("classify_query",  _classify)
    workflow.add_node("direct_answer",   _direct)
    workflow.add_node("run_standard",    _run_standard)
    workflow.add_node("plan_research",   _plan)
    workflow.add_node("run_subagent",    _subagent)
    workflow.add_node("synthesize",      _synthesize)
    workflow.add_node("critic",          _critic)

    workflow.set_entry_point("contextualize")
    workflow.add_edge("contextualize", "classify_query")
    workflow.add_conditional_edges(
        "classify_query",
        _route_by_mode,
        {
            "direct_answer":  "direct_answer",
            "run_standard":   "run_standard",
            "plan_research":  "plan_research",
        },
    )
    # FAST / STANDARD terminals.
    workflow.add_edge("direct_answer", END)
    workflow.add_edge("run_standard",  END)
    # DEEP: plan → Send(run_subagent) ... → synthesize → critic → END.
    workflow.add_conditional_edges(
        "plan_research", _fan_out_subagents, ["run_subagent"],
    )
    workflow.add_edge("run_subagent", "synthesize")
    workflow.add_edge("synthesize",   "critic")
    workflow.add_edge("critic",       END)

    return workflow.compile()
