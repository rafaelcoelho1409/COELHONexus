"""ycs/rag/adaptive — `build_adaptive_rag_graph()` — FAST/STANDARD/DEEP wiring.

Topology (deprecated `graphs/youtube/adaptive.py:L15-29`):

    START
      ↓
    contextualize
      ↓
    classify_query
      ├── FAST     → direct_answer → END
      ├── STANDARD → run_standard  → END
      └── DEEP     → plan_research → run_subagents (bounded fan-out) → synthesize → critic → END

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
grader sub-agent gate).

2026-09-16 — the fan-out itself is no longer a LangGraph `Send()`
conditional edge. A live DEEP run took 15m46s; tracing it back showed
Send's superstep barrier won't advance to `synthesize` until EVERY
sub-question reports back, and one sub-question can legitimately take
`run_subagent`'s documented worst case (~20.5 min) — so a single
degraded sub-question held the whole response hostage while the other
4 finished in seconds. `run_subagents` (plural) is now a plain node
that runs the fan-out itself via `run_subagents_bounded` (asyncio.wait
with an outer `DEEP_FANOUT_DEADLINE_S` deadline), so `synthesize`
always starts on time regardless of how many sub-questions are still
in flight."""
from __future__ import annotations

import asyncio
import logging
import os

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from domains.ycs.grader import DocumentGrader
from domains.ycs.rag.standard import build_youtube_rag_graph

from .nodes.classify import classify_query
from .nodes.contextualize import contextualize_question
from .nodes.critic import critic
from .nodes.direct_answer import direct_answer
from .nodes.plan import plan_research
from .nodes.run_standard import run_standard_pipeline
from .nodes.subagent import run_subagent, run_subagents_bounded
from .nodes.synthesize import synthesize
from .params import DEEP_FANOUT_DEADLINE_S, SUBAGENT_CONCURRENCY
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


def _route_after_direct(state: AdaptiveRAGState) -> str:
    """After FAST: success ends, failure falls back to STANDARD.

    2026-09-15 (DD placeholder principle — a degraded
    retrieval-grounded answer beats an error string): `direct_answer`
    marks every success `grounded=True` and every failure
    `grounded=False`, so a hung/failed fast call transparently retries
    as one STANDARD pass instead of surfacing
    "The model didn't respond..." to the user. No cycle risk —
    `run_standard` always terminates at END."""
    return "end" if state.get("grounded") else "run_standard"


def build_adaptive_rag_graph(
    retriever,
    grader: DocumentGrader,
    llm,
    checkpointer = None,
    neo4j_graph = None,
    llm_fast = None,
):
    """Build the Adaptive RAG parent graph.

    Wraps the STANDARD pipeline as a sub-graph and adds FAST (direct
    answer) and DEEP (multi-agent research) paths. Channel scope auto-
    detection runs in `classify_query` via `neo4j_graph`.

    `llm_fast` (2026-09-15): dedicated short-output client for
    `direct_answer` (own bandit cell + max_tokens cap) — falls back to
    `llm` when None so older callers keep working.

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
    async def _prepare(state):
        """CONTEXTUALIZE + CLASSIFY concurrently (2026-09-15, was serial).

        Both read the ORIGINAL question + history and write disjoint
        keys (contextualize: question/search_query/contextualized;
        classify: mode/sub_questions/channel_ids) — safe under gather.
        Classify resolves follow-ups against history inside its own
        prompt, so it no longer needs contextualize's rewrite first.
        Worst case drops from 30s + 30s serial to max(30s, 30s); the
        no-history fast path is unchanged (contextualize passthrough
        is instant, classify runs as before)."""
        import asyncio as _asyncio

        ctx_result, cls_result = await _asyncio.gather(
            contextualize_question(state, llm),
            classify_query(state, llm, neo4j_graph),
        )
        return {**ctx_result, **cls_result}

    async def _direct(state):
        return await direct_answer(state, llm_fast or llm)

    async def _run_standard(state, config: RunnableConfig):
        # `config` arg is auto-injected by LangGraph so we
        # can forward the user's `max_retries` down to the scoped
        # STANDARD sub-graph (previously the override silently fell
        # back to the sub-graph's default 3). See
        # `run_standard/node.py` for the recursion-budget rationale.
        scoped_graph = _build_standard_graph(state.get("channel_ids"))
        return await run_standard_pipeline(state, scoped_graph, config)

    async def _plan(state):
        return await plan_research(state, llm)

    async def _run_subagents(state: AdaptiveRAGState):
        """DEEP fan-out — bounded by `DEEP_FANOUT_DEADLINE_S`, NOT a
        LangGraph `Send()` (see `run_subagents_bounded`'s docstring for
        why: Send's superstep barrier can't proceed without every
        branch, so an outer deadline has to be enforced by this node
        itself via `asyncio.wait`, not the graph)."""
        channel_ids = state.get("channel_ids") or []
        parent_q    = state.get("question", "") or ""
        route       = state.get("route") or "search"
        thread_id   = state.get("thread_id") or ""

        async def _one(sub_q: str) -> dict:
            # Sub-agents inherit the channel scope from the parent
            # state. Concurrency is gated by a process-wide semaphore
            # (cap=5, matching the max sub-question count) so a typical
            # DEEP plan runs every sub-agent in one wave — the
            # scoped graph is built INSIDE the gate — StateGraph
            # compilation isn't free and we don't want to materialise N
            # sub-graphs for waiting sub-agents that haven't acquired
            # yet (only matters if the env override raises N above the
            # cap). Also forwards the parent rotator `llm` so the
            # sub-agent can run a single rephrased-question retry when
            # its first STANDARD invocation returns `no_docs` (see
            # `run_subagent`'s docstring + `subagent/prompts.py`).
            sem = _get_subagent_semaphore()
            async with sem:
                scoped_graph = _build_standard_graph(channel_ids)
                return await run_subagent(
                    {
                        "sub_question":    sub_q,
                        "parent_question": parent_q,
                        "channel_ids":     channel_ids,
                        "route":           route,
                        "thread_id":       thread_id,
                    },
                    scoped_graph, llm = llm,
                )

        return await run_subagents_bounded(
            state.get("sub_questions") or [],
            run_one    = _one,
            deadline_s = DEEP_FANOUT_DEADLINE_S,
            route      = route,
        )

    async def _synthesize(state):
        return await synthesize(state, llm)

    async def _critic(state):
        return await critic(state, llm)

    workflow.add_node("prepare",         _prepare)
    workflow.add_node("direct_answer",   _direct)
    workflow.add_node("run_standard",    _run_standard)
    workflow.add_node("plan_research",   _plan)
    workflow.add_node("run_subagents",   _run_subagents)
    workflow.add_node("synthesize",      _synthesize)
    workflow.add_node("critic",          _critic)

    workflow.set_entry_point("prepare")
    workflow.add_conditional_edges(
        "prepare",
        _route_by_mode,
        {
            "direct_answer":  "direct_answer",
            "run_standard":   "run_standard",
            "plan_research":  "plan_research",
        },
    )
    # FAST terminal on success, STANDARD fallback on failure.
    workflow.add_conditional_edges(
        "direct_answer",
        _route_after_direct,
        {
            "end":            END,
            "run_standard":   "run_standard",
        },
    )
    workflow.add_edge("run_standard",  END)
    # DEEP: plan → run_subagents (bounded fan-out) → synthesize → critic
    # → END. Plain edge, not a conditional `Send()` fan-out — the
    # bounded-deadline orchestration happens INSIDE `_run_subagents`
    # itself (see its docstring), not via LangGraph's own parallel-
    # branch machinery.
    workflow.add_edge("plan_research",  "run_subagents")
    workflow.add_edge("run_subagents",  "synthesize")
    workflow.add_edge("synthesize",     "critic")
    workflow.add_edge("critic",         END)

    return workflow.compile()
