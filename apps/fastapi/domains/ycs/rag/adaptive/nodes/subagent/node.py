"""ycs/rag/adaptive/nodes/subagent — DEEP-path fan-out target.

Each parallel sub-agent runs the STANDARD pipeline against ONE
sub-question. Receives a minimal `payload` dict (not the full parent
state) per LangGraph `Send()` semantics. Returns into `sub_results`
via the `operator.add` reducer declared in `state.py`.
The rephrase is delegated to an
    LLM call that swaps abstract framings for concrete vocabulary,
    closing the gap between abstract DEEP sub-questions and the
    literal phrasing in transcripts. See `prompts.py::REPHRASE_PROMPT`
    for the rationale.

Conventions: per `docs/CODE-CONVENTIONS.md` §2, prompts live in
`prompts.py`, loose tunables in `params.py`, the LangGraph wrapper
stays thin here."""
from __future__ import annotations
import domains
from domains.ycs.runtime.observability.service import traced
from .... import domain, service
from ... import params as _adaptive_params
from . import params, prompts

import asyncio
import logging


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
            f"{int(params.SUBAGENT_RUNTIME_TIMEOUT_S / 60)} min — likely a "
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


def _build_initial_state(
    sub_q: str,
    *,
    route: str = "",
    thread_id: str = "",
) -> dict:
    """Fresh STANDARD-graph state seeded for one sub-question.

    Pure helper — same shape used by both the first attempt AND the
    rephrased-question retry. Extracted so the retry branch can't
    drift from the first attempt's invariants."""
    return {
        "question":             sub_q,
        "thread_id":            thread_id,
        "route":                route,
        "mode":                 "deep",
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


# sub-agents pass `max_retries=1` to the STANDARD
# sub-graph instead of the default 3. Rationale: the parent planner
# already produced a focused sub-question — there's nothing to
# "rewrite" the way a freeform user query needs. One retry covers
# transient retrieve/grade noise; more attempts just eat the
# recursion budget. Combined with `SUBAGENT_RECURSION_LIMIT` (12) this
# caps the worst-case stuck sub-agent at ~2 minutes instead of the
# ~10 minutes seen before this commit.
_STANDARD_GRAPH_CONFIG = {
    "recursion_limit": _adaptive_params.SUBAGENT_RECURSION_LIMIT,
    "configurable":    {"max_retries": 1},
}


async def _run_standard_once(
    standard_graph,
    sub_q: str,
    *,
    route: str = "",
    thread_id: str = "",
) -> tuple[dict, BaseException | None]:
    """One bounded sub-graph invocation. Returns `(result, exc)`.

    Wraps the timeout + exception catch so the outer
    `run_subagent` can call this twice (first attempt + rephrase
    retry) without duplicating the boilerplate."""
    try:
        result = await asyncio.wait_for(
            standard_graph.ainvoke(
                _build_initial_state(
                    sub_q,
                    route = route,
                    thread_id = thread_id,
                ),
                config = _STANDARD_GRAPH_CONFIG,
            ),
            timeout = params.SUBAGENT_RUNTIME_TIMEOUT_S,
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
    chain = prompts.REPHRASE_PROMPT | llm
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "subagent_rephrase")
        response = await asyncio.wait_for(
            chain.ainvoke({
                "sub_question":    sub_q,
                "parent_question": parent_q,
            }),
            timeout = params.REPHRASE_TIMEOUT_S,
        )
        await service.capture_llm_usage(response)
    except (asyncio.TimeoutError, Exception) as e:
        logger.info(
            f"[ycs:subagent] rephrase failed for sub_q={sub_q[:60]!r}: "
            f"{type(e).__name__}: {e}"
        )
        return None
    rewritten = domain.strip_think_tags(response.content).strip().strip('"').strip("'")
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
    route     = str(payload.get("route") or "search")
    thread_id = str(payload.get("thread_id") or "")

    # First attempt — original phrasing.
    result, exc = await _run_standard_once(
        standard_graph,
        sub_q,
        route = route,
        thread_id = thread_id,
    )
    error_kind, answer_text = _classify_subagent_outcome(result, exc)

    # no_docs retry with a rephrased sub-question. The
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
                standard_graph,
                rewritten,
                route = route,
                thread_id = thread_id,
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

    domains.ycs.runtime.observability.metrics.record_subquestion(
        route = route,
        outcome = error_kind or "success",
    )
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


async def run_subagents_bounded(
    sub_questions:   list[str],
    *,
    run_one,
    deadline_s:      float,
    route:           str = "search",
) -> dict:
    """Fan out `run_one(sub_q)` over every sub-question with an OUTER
    wall-clock deadline, instead of relying purely on LangGraph's
    `Send()`/superstep barrier for the join — confirmed (2026-09-16
    /sota-search) that LangGraph only advances a superstep once EVERY
    parallel branch reports back, with no native "proceed without
    stragglers" option. Left unbounded, a single degraded sub-question
    can hold the WHOLE DEEP response hostage for up to `run_subagent`'s
    documented worst case (~20.5 min) even though the other 4 finished
    in seconds — this is what turned a real run into a 15m46s response.

    Uses `asyncio.wait`, not `asyncio.wait_for` + `gather`: `wait_for`
    cancels the entire gather — every child task, including ones about
    to finish — the instant the deadline hits. `asyncio.wait` only
    reports back `done`/`pending`, so sub-questions that finished
    within budget keep their real answer; only the ones STILL running
    at the deadline get cancelled and a placeholder instead.

    Live progress (2026-09-16): each item is also emitted via
    `get_stream_writer()` the instant its task settles — not just in
    the bulk return at the end — so the SSE layer (`stream_mode=
    ["updates", "custom"]`) can flip that card `queued→done` in real
    time while siblings are still researching. Best-effort: no writer
    outside a streaming run (e.g. sync `ainvoke`) means a silent
    no-op, never a failure."""
    if not sub_questions:
        return {"sub_results": []}
    try:
        from langgraph.config import get_stream_writer
        _writer = get_stream_writer()
    except Exception:
        _writer = None

    def _emit_live(item: dict) -> None:
        if _writer is None:
            return
        try:
            _writer({"sub_result": item})
        except Exception:
            pass

    def _item_from_done(t: asyncio.Task, q: str) -> dict:
        try:
            r = t.result()
            items = r.get("sub_results") or []
            if items:
                return items[0]
            raise ValueError("run_one returned no sub_results")
        except Exception as e:
            _ename = type(e).__name__
            logger.warning(
                f"[ycs:subagent] bounded fan-out: sub_q={q[:60]!r} "
                f"raised {_ename}: {e}"
            )
            domains.ycs.runtime.observability.metrics.record_subquestion(route = route, outcome = "hard_error")
            return {
                "sub_question":      q,
                "answer": (
                    f"_(this sub-question failed with an error: "
                    f"`{_ename}`.)_"
                ),
                "citations":         [],
                "grounded":          False,
                "retrieval_sources": [],
                "error_kind":        "hard_error",
            }

    import time as _time
    sub_results: list[dict] = []
    remaining_tasks = {asyncio.ensure_future(run_one(q)): q for q in sub_questions}
    _start = _time.monotonic()
    try:
        while remaining_tasks:
            _elapsed = _time.monotonic() - _start
            _budget = deadline_s - _elapsed
            if _budget <= 0:
                break
            done, pending = await asyncio.wait(
                remaining_tasks.keys(), timeout = _budget,
                return_when = asyncio.FIRST_COMPLETED,
            )
            if not done:
                break  # budget expired with nothing new settling
            for t in done:
                q = remaining_tasks.pop(t)
                item = _item_from_done(t, q)
                sub_results.append(item)
                _emit_live(item)
    except asyncio.CancelledError:
        # Stop button / client disconnect: the SSE generator cancels the
        # graph producer, which lands here. `asyncio.wait` does NOT cancel
        # the children itself — without this they keep burning rotator
        # slots for results nobody will read. Cancel + settle, re-raise.
        for t in remaining_tasks:
            t.cancel()
        await asyncio.gather(*remaining_tasks.keys(), return_exceptions = True)
        raise

    if remaining_tasks:
        logger.warning(
            f"[ycs:subagent] bounded fan-out: {len(remaining_tasks)}/"
            f"{len(sub_questions)} sub-question(s) still running at "
            f"the {int(deadline_s)}s deadline — cancelling + "
            f"placeholding"
        )
        for t, q in list(remaining_tasks.items()):
            t.cancel()
            domains.ycs.runtime.observability.metrics.record_subquestion(route = route, outcome = "deadline")
            item = {
                "sub_question": q,
                "answer": (
                    "_(this sub-question was still researching when "
                    "the overall research deadline was reached — the "
                    "other sub-questions below are complete. Re-asking "
                    "may give this one enough time, or land on a "
                    "faster rotator arm.)_"
                ),
                "citations":         [],
                "grounded":          False,
                "retrieval_sources": [],
                "error_kind":        "deadline",
            }
            sub_results.append(item)
            _emit_live(item)
        # Let the cancellations actually settle before returning — an
        # un-awaited cancelled task keeps running in the background,
        # burning rotator slots for a result nobody will see.
        await asyncio.gather(*remaining_tasks.keys(), return_exceptions = True)

    return {"sub_results": sub_results}
