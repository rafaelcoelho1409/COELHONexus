"""ycs/rag/standard/nodes/corroborate — post-hallucination-check web
corroboration node.

Fires ONLY from `graph.py::_decide_after_hallucination_check`'s
"ungrounded, retries exhausted" branch — the exact moment the graph
was about to ship an unverified answer unchanged. Runs ONE Parallel
web search (`domains.ycs.rag.web_search.search_web`, same free
keyless MCP endpoint `fallback_answer` uses) + ONE short LLM judgment
call, then appends a one-sentence corroboration/contradiction note to
the EXISTING answer. Never regenerates the answer, never blocks on
failure — any error, empty search, or 'unclear' verdict passes the
generation through byte-for-byte unchanged.

Low-frequency by construction (same reasoning as `fallback_answer`):
only the rare case where grade_documents found SOME documents but
check_hallucination still couldn't verify the answer against them
after every rewrite retry. Shares Parallel's rate-limit budget with
`fallback_answer`, not a separate allowance."""
from __future__ import annotations

import asyncio

from domains.ycs.rag.llm_call import resilient_ainvoke
from domains.ycs.rag.web_search import search_web
from domains.ycs.runtime.llm_counter import set_node as _llm_set_node
from domains.ycs.runtime.observability import traced

from ...state import YouTubeRAGState
from .prompts import CORROBORATION_PROMPT
from .schemas import CorroborationResult


# Own budget, separate from `check_hallucination`'s 45s and
# `fallback_answer`'s 60s — this node does a search THEN a judge call,
# so it needs headroom for both, but this is still a rare best-effort
# safety net, not a path worth waiting on indefinitely.
_JUDGE_TIMEOUT_S = 30.0


@traced("rag.corroborate")
async def corroborate_claim(state: YouTubeRAGState, llm) -> dict:
    """Best-effort external corroboration/contradiction check for an
    answer the grounding judge couldn't verify. Returns `{}` (no
    state change) on ANY failure or inconclusive result — the caller
    graph edges straight to `format_citations` either way, so a no-op
    here just means the answer ships as it already was."""
    question   = state["question"]
    generation = state.get("generation") or ""
    if not generation.strip():
        return {}

    web_context = await search_web(question, session_id = state.get("thread_id"))
    if not web_context.strip():
        return {}

    chain = CORROBORATION_PROMPT | llm.with_structured_output(
        CorroborationResult,
    )
    try:
        _llm_set_node(node = "corroborate")
        result: CorroborationResult = await resilient_ainvoke(
            chain,
            {
                "question":    question,
                "generation":  generation,
                "web_context": web_context,
            },
            operation    = "corroborate",
            timeout_s    = _JUDGE_TIMEOUT_S,
            max_attempts = 2,
        )
    except (asyncio.TimeoutError, Exception):
        # Transient judge failure — ship the original answer, don't
        # block or degrade it over a best-effort check that failed.
        return {}

    note = (result.note or "").strip()
    if result.verdict == "unclear" or not note:
        return {}

    icon = "✅" if result.verdict == "corroborates" else "⚠️"
    return {"generation": f"{generation}\n\n{icon} {note}"}
