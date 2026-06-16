"""ycs/rag/adaptive/nodes/subagent — DEEP-path fan-out target.

Each parallel sub-agent runs the STANDARD pipeline against ONE
sub-question. Receives a minimal `payload` dict (not the full parent
state) per LangGraph `Send()` semantics. Returns into `sub_results`
via the `operator.add` reducer declared in `state.py`.

Direct port of deprecated `graphs/youtube/adaptive.py:L226-265`,
extended 2026-06-16 with:
  - per-invocation total-runtime timeout (~10 min ceiling on the
    sub-graph) so a stuck sub-agent can't block every other in
    sequential mode,
  - structured `error_kind` field on the returned sub_result so the
    UI placeholder is specific (`timeout` / `recursion_limit` /
    `no_docs` / `hard_error`) instead of a generic "rotator
    exhausted" guess,
  - ONE rephrased-question retry when the first attempt comes back
    with `error_kind="no_docs"`. The rephrase is delegated to an
    LLM call that swaps abstract framings for concrete vocabulary,
    closing the gap between abstract DEEP sub-questions and the
    literal phrasing in transcripts. See `prompts.py::REPHRASE_PROMPT`
    for the rationale.

Conventions: per `docs/CODE-CONVENTIONS.md` §2, prompts live in
`prompts.py`, loose tunables in `params.py`, the LangGraph wrapper
stays thin here."""
from __future__ import annotations

import asyncio
import logging

from domains.ycs.runtime.observability import traced

from ....domain import strip_think_tags
from ...params import SUBGRAPH_RECURSION_LIMIT
from .params import REPHRASE_TIMEOUT_S, SUBAGENT_RUNTIME_TIMEOUT_S
from .prompts import REPHRASE_PROMPT


logger = logging.getLogger(__name__)


def _classify_subagent_outcome(
    result: dict, exc: BaseException | None,
) -> tuple[str, str]:
    """Return `(error_kind, answer_text)` for the sub_result payload.

    `error_kind` is one of: ``""`` (success), ``"timeout"``,
    ``"recursion_limit"``, ``"no_docs"``, ``"hard_error"``. The
    caller embeds this in `sub_results` so `_thinking_apply` can
    render a specific UI placeholder instead of the generic
    "rotator exhausted" fallback.

    `answer_text` is the human-facing message — the model's
    actual generation when present, or a tight failure-mode
    string when it isn't (so a DONE card always shows SOMETHING
    in the expander)."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout", (
            "_(this sub-question timed out after "
            f"{int(SUBAGENT_RUNTIME_TIMEOUT_S / 60)} min — likely a "
            "single node hung silently. Re-asking often picks a "
            "different rotator arm and completes.)_"
        )
    if isinstance(exc, Exception):
        msg = str(exc).strip()
        # LangGraph raises this exact text for recursion-limit hits.
        if "recursion limit" in msg.lower():
            return "recursion_limit", (
                "_(this sub-question hit the sub-graph recursion "
                "limit — the rewrite/retrieve loop didn't converge on "
                "useful evidence. Try rephrasing the question to be "
                "more specific.)_"
            )
        return "hard_error", (
            f"_(this sub-question failed with an error: "
            f"`{type(exc).__name__}: {msg[:120]}`. Re-asking will "
            "retry from scratch.)_"
        )
    gen = (result.get("generation") or "").strip()
    if gen:
        return "", gen
    # The graph completed but the generator emitted nothing — typically
    # means grading dropped every retrieved doc and the conditional
    # edge took the `end` path with `documents=[]`.
    return "no_docs", (
        "_(this sub-question found no relevant transcript evidence "
        "after retrieval + grading. The question may not be covered "
        "by the indexed videos, or the rewrite loop couldn't find a "
        "useful search query.)_"
    )


def _build_initial_state(sub_q: str) -> dict:
    """Fresh STANDARD-graph state seeded for one sub-question.

    Pure helper — same shape used by both the first attempt AND the
    rephrased-question retry. Extracted so the retry branch can't
    drift from the first attempt's invariants."""
    return {
        "question":             sub_q,
        "documents":            [],
        "generation":           "",
        "retry_count":          0,
        "search_query":         sub_q,
        "grounded":             False,
        "citations":            [],
        "retrieval_sources":    [],
        # Sub-agents intentionally see NO history — their sub-question is
        # self-contained by construction. Conversation context is only
        # injected at the user-facing synthesize step (one level up).
        "conversation_history": [],
    }


# 2026-06-15 — sub-agents pass `max_retries=1` to the STANDARD
# sub-graph instead of the default 3. Rationale: the parent planner
# already produced a focused sub-question — there's nothing to
# "rewrite" the way a freeform user query needs. One retry covers
# transient retrieve/grade noise; more attempts just eat the
# recursion budget. Combined with `SUBGRAPH_RECURSION_LIMIT` (12 in
# 2026-06-16) this caps the worst-case stuck sub-agent at ~2 minutes
# instead of the ~10 minutes seen before this commit.
_STANDARD_GRAPH_CONFIG = {
    "recursion_limit": SUBGRAPH_RECURSION_LIMIT,
    "configurable":    {"max_retries": 1},
}


async def _run_standard_once(
    standard_graph, sub_q: str,
) -> tuple[dict, BaseException | None]:
    """One bounded sub-graph invocation. Returns `(result, exc)`.

    Wraps the timeout + exception catch so the outer
    `run_subagent` can call this twice (first attempt + rephrase
    retry) without duplicating the boilerplate."""
    try:
        result = await asyncio.wait_for(
            standard_graph.ainvoke(
                _build_initial_state(sub_q),
                config = _STANDARD_GRAPH_CONFIG,
            ),
            timeout = SUBAGENT_RUNTIME_TIMEOUT_S,
        )
        return result, None
    except (asyncio.TimeoutError, Exception) as e:
        return {
            "citations":         [],
            "grounded":          False,
            "retrieval_sources": [],
        }, e


async def _rephrase_subquestion(
    sub_q: str, parent_q: str, llm,
) -> str | None:
    """Ask the rotator to rewrite `sub_q` with vocabulary that's more
    likely to match transcript phrasing. Returns the rewrite, or
    `None` if anything fails (rotator exhausted, timeout, empty
    response, or the model echoed the original word-for-word).

    Best-effort by design — a missing rephrase just means we skip
    the no_docs retry and report the first attempt's placeholder."""
    if llm is None:
        return None
    chain = REPHRASE_PROMPT | llm
    try:
        response = await asyncio.wait_for(
            chain.ainvoke({
                "sub_question":    sub_q,
                "parent_question": parent_q,
            }),
            timeout = REPHRASE_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, Exception) as e:
        logger.info(
            f"[ycs:subagent] rephrase failed for sub_q={sub_q[:60]!r}: "
            f"{type(e).__name__}: {e}"
        )
        return None
    rewritten = strip_think_tags(response.content).strip().strip('"').strip("'")
    if not rewritten or rewritten.lower() == sub_q.lower():
        return None
    return rewritten


@traced("rag.subagent")
async def run_subagent(
    payload: dict, standard_graph, llm = None,
) -> dict:
    """Run the STANDARD pipeline for one sub-question, then project the
    result into a `sub_results` entry.

    `llm` is optional — when present, a `no_docs` first-attempt
    triggers one rephrased-question retry. Pass `None` to disable
    the retry path (e.g. in unit tests, or if the rotator is known
    to be exhausted). The retry is gated on first-attempt outcome
    being EXACTLY `no_docs`; timeouts and hard errors don't get a
    second chance because the failure mode isn't about question
    framing."""
    sub_q     = payload["sub_question"]
    parent_q  = payload.get("parent_question", "") or sub_q

    # First attempt — original phrasing.
    result, exc = await _run_standard_once(standard_graph, sub_q)
    error_kind, answer_text = _classify_subagent_outcome(result, exc)

    # 2026-06-16 — no_docs retry with a rephrased sub-question. The
    # initial phrasing typically lost on abstract terms not present
    # in the transcripts; the rephrase swaps in concrete vocabulary
    # (see `prompts.py::REPHRASE_PROMPT`). One retry only — keeps
    # the worst-case sub-agent wall-time bounded at
    # 2 × SUBAGENT_RUNTIME_TIMEOUT_S + REPHRASE_TIMEOUT_S ≈ 20.5 min,
    # still under the parent watchdog's 15-min event-silence ceiling
    # because the rephrase + second invocation yield no parent events
    # but heartbeat keeps the SSE alive.
    retry_note = ""
    if error_kind == "no_docs" and llm is not None:
        rewritten = await _rephrase_subquestion(sub_q, parent_q, llm)
        if rewritten:
            logger.info(
                f"[ycs:subagent] retrying sub_q={sub_q[:80]!r} "
                f"as {rewritten[:80]!r}"
            )
            result2, exc2 = await _run_standard_once(
                standard_graph, rewritten,
            )
            error_kind2, answer_text2 = _classify_subagent_outcome(
                result2, exc2,
            )
            if error_kind2 == "":
                # Retry produced a real answer — adopt it, note the rewrite.
                error_kind  = ""
                result      = result2
                retry_note  = (
                    f"\n\n_(answered after rephrasing the sub-question to: "
                    f"{rewritten})_"
                )
                answer_text = answer_text2 + retry_note

    return {
        "sub_results": [{
            "sub_question":      sub_q,
            "answer":            answer_text,
            "citations":         result.get("citations", []),
            "grounded":          result.get("grounded", False),
            "retrieval_sources": result.get("retrieval_sources", []),
            "error_kind":        error_kind,
        }],
    }
