"""ycs/rag/standard — `build_youtube_rag_graph()` — STANDARD pipeline wiring.

Graph topology (deprecated `graphs/youtube/rag.py:L240-243`,
extended 2026-06-16 with the CRAG-style fallback rescue branch, and
2026-09-16 with the CRAG "Ambiguous" corroboration branch):

    retrieve → grade_documents
       ↑              ├─ documents kept     → generate → check_hallucination
       │              ├─ no docs, retry     → rewrite_query
       │              └─ no docs, exhausted → fallback_answer → END
       │
       └──────────────────────────────── rewrite_query
                                                ↑
              check_hallucination ─┬─ grounded              → format_citations → END
                                    ├─ ungrounded, retry     → rewrite_query
                                    └─ ungrounded, exhausted → corroborate → format_citations → END

Builder is a function (not a class) — the deprecated wrapped these in
a `YouTubeContentGraph` class but it held no state; we collapse it.

The compiled graph is **uncompiled with a checkpointer** to match
deprecated convention: the deprecated comment notes `AsyncRedisSaver
causes deadlock when called from endpoint handlers within the lifespan
async-with block`. A `checkpointer` parameter is accepted but unused
to preserve the deprecated public signature.

2026-06-16 — graceful-degradation rescue. Previously the "no docs,
retries exhausted" path routed straight to END with `generation=""`,
which the SSE layer translated into the
"(no response — see Thinking for pipeline status)" sentinel. Per
CRAG (Yan et al. 2024) + production agentic-RAG guidance (Backblaze
2026, Cognito-LangGraph 2026), the correct shape is to always
produce a real answer — even an honest "I couldn't ground this in
the corpus, but here's what I can tell you from conversation + general
knowledge." That's `nodes/fallback_answer/`. Covers meta-questions,
out-of-corpus topics, embedding misses, and over-strict grader rejects
in one path — no per-question-type rules required.

2026-09-16 — corroboration branch. `fallback_answer` covers CRAG's
"Incorrect" action (retrieval found nothing). This closes "Ambiguous":
retrieval DID find documents, `generate` produced an answer from them,
but `check_hallucination` couldn't verify it and the rewrite budget is
spent. Previously that answer shipped unchanged — silently. Per 2026
agentic-RAG production guidance (a groundedness judge should gate the
final answer, not just log a warning), `nodes/corroborate/` now runs
ONE live web search + a fact-check judgment on that specific answer,
appending a one-sentence "external sources support/conflict with
this" note when the web evidence is decisive. Degrades to a silent
no-op on any failure or genuinely inconclusive result — this can only
ever ADD a signal, never block or replace the answer."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from domains.ycs.grader import DocumentGrader

from .nodes.cite import format_citations
from .nodes.corroborate import corroborate_claim
from .nodes.fallback_answer import fallback_answer
from .nodes.generate import generate
from .nodes.grade import grade_documents
from .nodes.hallucination import check_hallucination
from .nodes.retrieve import retrieve
from .nodes.rewrite import rewrite_query
from .params import DEFAULT_MAX_RETRIES
from .state import YouTubeRAGState


def _decide_after_grading(
    state: YouTubeRAGState, config: RunnableConfig,
) -> str:
    """Generate when docs survived grading; rewrite while retries
    remain; fall back to a no-evidence rescue answer when retries
    are exhausted (the CRAG graceful-degradation branch — see
    `nodes/fallback_answer/` for the production rationale)."""
    if state["documents"]:
        return "generate"
    max_retries = config.get("configurable", {}).get(
        "max_retries", DEFAULT_MAX_RETRIES,
    )
    if state.get("retry_count", 0) < max_retries:
        return "rewrite"
    return "fallback"


def _decide_after_hallucination_check(
    state: YouTubeRAGState, config: RunnableConfig,
) -> str:
    """Accept on grounded; rewrite while retries remain; once
    exhausted, run ONE external corroboration check instead of
    silently accepting the ungrounded answer (2026-09-16 — was a
    direct route to `format_citations`; deprecated rationale for
    accepting rather than looping forever still holds: don't trap the
    user in an infinite loop — best-effort answer + cite-what-we-have
    — but "best-effort" now includes a web cross-check first, closing
    CRAG's "Ambiguous" gap: `corroborate_claim` degrades to a no-op
    on any failure, so this is strictly additive, never a new way to
    block the answer)."""
    if state.get("grounded", False):
        return "format_citations"
    max_retries = config.get("configurable", {}).get(
        "max_retries", DEFAULT_MAX_RETRIES,
    )
    if state.get("retry_count", 0) < max_retries:
        return "rewrite"
    return "corroborate"


def build_youtube_rag_graph(
    retriever,
    grader: DocumentGrader,
    llm,
    checkpointer = None,
    channel_ids: list[str] | None = None,
):
    """Build + compile the STANDARD pipeline.

    `retriever`, `grader`, `llm`, `channel_ids` are captured by inner
    async closures that bind the node functions' deps — LangGraph
    requires `add_node` to receive a true async callable (not a
    `functools.partial` or sync wrapper), so we define real `async def`
    locals.

    `checkpointer` is accepted but currently unused (preserved
    parameter for API compatibility — deprecated `rag.py:L305-307`
    documented the AsyncRedisSaver deadlock the same way)."""
    workflow = StateGraph(YouTubeRAGState)

    async def _retrieve(state):
        return await retrieve(state, retriever, channel_ids)

    async def _grade(state):
        return await grade_documents(state, grader)

    async def _generate(state):
        return await generate(state, llm)

    async def _check_hallucination(state):
        return await check_hallucination(state, llm)

    async def _rewrite(state):
        return await rewrite_query(state, llm)

    async def _fallback(state):
        # CRAG graceful-degradation rescue. Receives the
        # same `YouTubeRAGState` every other node sees so it can read
        # `conversation_history` for meta / follow-up resolution and
        # `question` for the literal user intent.
        return await fallback_answer(state, llm)

    async def _corroborate(state):
        # CRAG "Ambiguous" handling — see `_decide_after_hallucination_
        # check`'s docstring. Distinct from `_fallback`: this fires
        # when retrieval DID find documents but the grounding judge
        # couldn't verify the generated answer against them; fallback
        # fires when retrieval found nothing at all.
        return await corroborate_claim(state, llm)

    workflow.add_node("retrieve",            _retrieve)
    workflow.add_node("grade_documents",     _grade)
    workflow.add_node("generate",            _generate)
    workflow.add_node("check_hallucination", _check_hallucination)
    workflow.add_node("format_citations",    format_citations)
    workflow.add_node("rewrite_query",       _rewrite)
    workflow.add_node("fallback_answer",     _fallback)
    workflow.add_node("corroborate",         _corroborate)

    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "grade_documents")
    workflow.add_conditional_edges(
        "grade_documents",
        _decide_after_grading,
        {
            "generate": "generate",
            "rewrite":  "rewrite_query",
            "fallback": "fallback_answer",
        },
    )
    workflow.add_edge("generate", "check_hallucination")
    workflow.add_conditional_edges(
        "check_hallucination",
        _decide_after_hallucination_check,
        {
            "format_citations": "format_citations",
            "rewrite":          "rewrite_query",
            "corroborate":      "corroborate",
        },
    )
    workflow.add_edge("format_citations", END)
    workflow.add_edge("fallback_answer",   END)
    workflow.add_edge("rewrite_query",     "retrieve")
    workflow.add_edge("corroborate",       "format_citations")

    return workflow.compile()
