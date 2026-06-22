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
sub-graph built from the parent state's `channel_ids`.

2026-06-16 — `run_subagent` fan-out is gated by a process-wide
`asyncio.Semaphore` sized at `SUBAGENT_CONCURRENCY=5` (the max
sub-question count). DEEP plans now run ALL sub-agents in a single
parallel wave instead of N sequential waves, cutting DEEP wall-time
~3-5×. The semaphore is process-wide rather than per-request so two
concurrent users on the same worker still respect the cap. See
`params.py::SUBAGENT_CONCURRENCY` for the rotator-parallelism
safety analysis (provider distribution + per-arm 60s cooldown +
grader sub-agent gate)."""
from __future__ import annotations

import asyncio
import logging
import os

from langchain_core.runnables import RunnableConfig
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
from .params import SUBAGENT_CONCURRENCY
from .state import AdaptiveRAGState


logger = logging.getLogger(__name__)


def _resolve_subagent_concurrency() -> int:
    """Env override `KD_SUBAGENT_CONCURRENCY` wins over the default,
    floored at 1 so a misconfigured `0` never deadlocks the fan-out."""
    if "KD_SUBAGENT_CONCURRENCY" in os.environ:
        try:
            return max(1, int(os.environ["KD_SUBAGENT_CONCURRENCY"]))
        except (TypeError, ValueError):
            pass
    return max(1, SUBAGENT_CONCURRENCY)


# Process-wide semaphore shared across ALL in-flight Ask requests. Two
# concurrent DEEP runs (e.g. two users / two tabs) together can hold at
# most `SUBAGENT_CONCURRENCY` sub-agents, so a single worker can never
# blow past the rotator's per-minute rate-window budget — multi-user
# bursts get queued, not multiplied. Lazily constructed on first acquire
# because `asyncio.Semaphore()` at import time would bind to the wrong /
# no event loop on Python < 3.10. `_subagent` checks/initialises under
# the same module import, single-threaded → no race.
_subagent_semaphore: asyncio.Semaphore | None = None


def _get_subagent_semaphore() -> asyncio.Semaphore:
    global _subagent_semaphore
    if _subagent_semaphore is None:
        n = _resolve_subagent_concurrency()
        _subagent_semaphore = asyncio.Semaphore(n)
        logger.info(
            f"[ycs:adaptive] sub-agent concurrency cap = {n} "
            f"(KD_SUBAGENT_CONCURRENCY override active)"
            if "KD_SUBAGENT_CONCURRENCY" in os.environ
            else f"[ycs:adaptive] sub-agent concurrency cap = {n}"
        )
    return _subagent_semaphore


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
    scope + the parent question (2026-06-16: used by the sub-agent's
    `no_docs` rephrase retry to anchor the rewrite to the original
    intent — see `nodes/subagent/node.py::_rephrase_subquestion`)."""
    channel_ids = state.get("channel_ids") or []
    parent_q    = state.get("question", "") or ""
    return [
        Send(
            "run_subagent",
            {
                "sub_question":    q,
                "parent_question": parent_q,
                "channel_ids":     channel_ids,
                "route":           state.get("route") or "search",
                "thread_id":       state.get("thread_id") or "",
            },
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

    async def _run_standard(state, config: RunnableConfig):
        # 2026-06-16 — `config` arg is auto-injected by LangGraph so we
        # can forward the user's `max_retries` down to the scoped
        # STANDARD sub-graph (previously the override silently fell
        # back to the sub-graph's default 3). See
        # `run_standard/node.py` for the recursion-budget rationale.
        scoped_graph = _build_standard_graph(state.get("channel_ids"))
        return await run_standard_pipeline(state, scoped_graph, config)

    async def _plan(state):
        return await plan_research(state, llm)

    async def _subagent(payload):
        # Sub-agents inherit the channel scope from the parent state.
        # Concurrency is gated by a process-wide semaphore (cap=5,
        # matching the max sub-question count) so a typical DEEP plan
        # runs all sub-agents in a single parallel wave. Build the
        # scoped graph INSIDE the gate — the StateGraph compilation
        # isn't free and we don't want to materialise N sub-graphs for
        # waiting sub-agents that haven't acquired yet (only matters
        # if the env override raises N above the cap).
        #
        # 2026-06-16 — also forward the parent rotator `llm` so the
        # sub-agent can run a single rephrased-question retry when its
        # first STANDARD invocation returns `no_docs`. The retry path
        # lives inside `run_subagent` (see its docstring + the
        # subagent/prompts.py rephrase rationale).
        sem = _get_subagent_semaphore()
        async with sem:
            channel_ids = payload.get("channel_ids")
            scoped_graph = _build_standard_graph(channel_ids)
            return await run_subagent(payload, scoped_graph, llm = llm)

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
