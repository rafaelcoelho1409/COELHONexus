"""ycs/agents — agentic RAG router: ask (sync+stream), ingest, graph stats, pipeline."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from domains.ycs.runtime.observability import record_ask_run
from infra.langfuse import (
    set_current_span_langfuse_io,
    set_current_span_langfuse_observation_metadata,
    set_current_span_langfuse_trace_metadata,
)
from infra.otel import get_tracer

from domains.ycs.cache import cache_response, get_cached_response
from domains.ycs.conversation import (
    DEFAULT_THREAD_ID,
    branch_thread,
    delete_thread,
    delete_turn,
    get_history,
    get_thread_locked_scope,
    insert_turn,
    list_thread_messages,
    list_threads,
    save_turn,
    update_turn_answer,
)
from domains.ycs.graph_builder import get_graph_stats
from domains.ycs.runtime.llm_counter import (
    clear_state as _llm_counter_reset,
    diff_usage as _llm_diff_usage,
    read_counters as _llm_read_counters,
    set_node as _llm_set_node,
    set_thread as _llm_set_thread,
)

from .build import _serialize_update, build_graph_from_request
from .schemas import (
    GraphIngestRequest,
    IngestRequest,
    PipelineRequest,
    RAGSearchRequest,
)


router = APIRouter()
logger = logging.getLogger(__name__)

# Min gap between incremental Postgres writes; smaller = more live, larger = fewer PG round-trips.
_STREAM_PERSIST_INTERVAL_S = 2.5

# If no astream() event arrives within this window at bootstrap, fall back to ainvoke()
# (local k3d hangs before the first stream event while ainvoke completes normally).
# 2026-09-16: 15s → 60s. 15s assumed a healthy rotator (prepare = 2 fast LLM
# calls, first event in seconds). Observed live on a degraded rotator: prepare
# alone exceeds 15s while the graph is healthy-but-slow, so the fallback fired
# spuriously and switched a good stream to blind ainvoke — plan cards never
# painted, only the spinner, until the whole DEEP run landed at once. 60s keeps
# the live path (plan render + per-card custom flips) through slow patches;
# heartbeats keep TCP alive meanwhile, and the 15-min watchdog still guards a
# genuinely hung producer. Tradeoff accepted: a true k3d-level hang now costs
# 60s of spinner before fallback instead of 15s.
_ASTREAM_BOOTSTRAP_FALLBACK_S = 60.0
_ASTREAM_BOOTSTRAP_FALLBACK_TICKS = max(
    1, int(_ASTREAM_BOOTSTRAP_FALLBACK_S / _STREAM_PERSIST_INTERVAL_S),
)

# 15 min ≈ 3× the slowest DEEP sub-agent (recursion_limit=12, cap=3).
_LANGGRAPH_WATCHDOG_S = 15 * 60.0

# Global per-request deadlines by requested mode (2026-09-15): bounds
# the total grind during provider outages — DD's backstop principle at
# graph scope. Auto gets the roomy default since it may classify deep;
# forced modes get exact budgets. On expiry the request serves ONE
# bounded `fallback_answer` pass (general knowledge + whatever was
# asked) instead of spinning until the client gives up.
_ASK_DEADLINE_S = {
    "fast":     300.0,
    "standard": 600.0,
    "deep":     1500.0,
}
_ASK_DEADLINE_DEFAULT_S = 900.0

# Hung PG connection blocks the heartbeat.
_PERSIST_TIMEOUT_S = 3.0

# In-memory per-pod; upgrade to Redis for >1 worker.
_CANCELLED_TURN_IDS: set[int] = set()


def _langfuse_ycs_input(
    *,
    question: str,
    route: str,
    force_mode: str,
    channel_ids: list[str],
    thread_id: str,
) -> dict:
    return {
        "question": question,
        "route": route,
        "force_mode": force_mode or "",
        "channel_ids": list(channel_ids or []),
        "thread_id": thread_id,
    }


def _langfuse_ycs_output(
    *,
    status: str,
    answer: str,
    mode: str,
    grounded: bool,
    citations: list,
    sub_questions: list | None = None,
    confidence_score: float | None = None,
    error: str | None = None,
) -> dict:
    output = {
        "status": status,
        "answer": answer[:4000],
        "mode": mode or "",
        "grounded": bool(grounded),
        "citation_count": len(citations or []),
    }
    if sub_questions is not None:
        output["sub_question_count"] = len(sub_questions or [])
    if confidence_score is not None:
        output["confidence_score"] = confidence_score
    if error:
        output["error"] = error
    return output


def _result_to_graph_updates(result: dict[str, Any]) -> list[dict[str, dict[str, Any]]]:
    """Synthesize ainvoke() result into node-update events for the SSE bootstrap fallback."""
    updates: list[dict[str, dict[str, Any]]] = []
    mode = str(result.get("mode") or "").strip().lower()

    classify_update: dict[str, Any] = {}
    if mode:
        classify_update["mode"] = mode
    if mode == "deep":
        sub_questions = result.get("sub_questions") or []
        if sub_questions:
            classify_update["sub_questions"] = sub_questions
    if classify_update:
        updates.append({"classify_query": classify_update})

    if mode == "deep":
        plan_update: dict[str, Any] = {}
        sub_questions = result.get("sub_questions") or []
        if sub_questions:
            plan_update["sub_questions"] = sub_questions
        research_plan = str(result.get("research_plan") or "").strip()
        if research_plan:
            plan_update["research_plan"] = research_plan
        if plan_update:
            updates.append({"plan_research": plan_update})
        for item in result.get("sub_results") or []:
            if isinstance(item, dict):
                updates.append({"run_subagent": {"sub_results": [item]}})
        synth_update: dict[str, Any] = {}
        generation = str(result.get("generation") or "")
        if generation:
            synth_update["generation"] = generation
        citations = result.get("citations")
        if isinstance(citations, list) and citations:
            synth_update["citations"] = citations
        if synth_update:
            updates.append({"synthesize": synth_update})
        if result.get("confidence_score") is not None:
            updates.append({
                "critic": {"confidence_score": result.get("confidence_score")},
            })
        return updates

    terminal_node = "direct_answer" if mode == "fast" else "run_standard"
    terminal_update: dict[str, Any] = {}
    generation = str(result.get("generation") or "")
    if generation:
        terminal_update["generation"] = generation
    citations = result.get("citations")
    if isinstance(citations, list) and citations:
        terminal_update["citations"] = citations
    search_query = str(result.get("search_query") or "").strip()
    if search_query:
        terminal_update["search_query"] = search_query
    if terminal_update:
        updates.append({terminal_node: terminal_update})
    return updates


@router.get("/usage/{thread_id}")
async def get_thread_usage(thread_id: str) -> dict:
    """Aggregate LLM usage for one Ask conversation (models + tokens per
    node, and totals) — mirrors Ingestion's per-video LLM drawer."""
    return await _llm_read_counters(thread_id)


@router.post("/rotator/ping")
async def rotator_ping(request: Request) -> dict:
    """Connectivity check against the live rotator chain."""
    import time
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        return {
            "status": "error",
            "error":  "rotator chain not initialized",
        }
    start = time.monotonic()
    try:
        response = await llm.ainvoke("ping")
    except Exception as e:
        return {
            "status": "error",
            "error":  f"{type(e).__name__}: {str(e)[:300]}",
        }
    elapsed_ms = int((time.monotonic() - start) * 1000)
    reply = getattr(response, "content", "") or ""
    if isinstance(reply, list):
        reply = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in reply
        )
    # 2026-09-18: was a hardcoded "rotator (FGTS-VA across 7 providers)"
    # string — a stale, client-side guess at the external rotator's own
    # arm count, which lives entirely in that separate repo now and can
    # change without this client knowing. Report the REAL resolved
    # deployment from response_metadata instead, same as every other
    # model-name read this session (capture_llm_usage, code_synth's
    # _resolve_model_id) — actually informative for a connectivity check.
    meta = getattr(response, "response_metadata", None) or {}
    model = meta.get("model_name") or meta.get("model") or "unknown"
    return {
        "status": "ok",
        "model":  model,
        "ms":     elapsed_ms,
        "reply":  str(reply)[:200],
    }


@router.get("/history/{thread_id}")
async def get_thread_history(thread_id: str, request: Request) -> dict:
    """Return Q+A history for thread_id; empty list for default sentinel or unknown thread."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return {"thread_id": thread_id, "items": [], "total": 0}
    items = await list_thread_messages(
        request.app.state.pg_url, thread_id,
    )
    return {"thread_id": thread_id, "items": items, "total": len(items)}


@router.get("/threads")
async def get_threads(request: Request) -> dict:
    """List existing threads most-recent first; each item has thread_id/turn_count/last_seen/first_question."""
    items = await list_threads(request.app.state.pg_url)
    return {"items": items, "total": len(items)}


@router.post("/threads/{thread_id}/branch")
async def branch_thread_endpoint(
    thread_id: str,
    request:   Request,
) -> dict:
    """Branch a thread at up_to_created_at (copies whole source if absent); returns new_thread_id + copied count."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        raise HTTPException(
            status_code = 400,
            detail      = "cannot branch the default sentinel",
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    up_to = (body.get("up_to_created_at") or "").strip() or None
    new_thread_id = (uuid.uuid4().hex)[:12]
    n = await branch_thread(
        request.app.state.pg_url, thread_id, up_to, new_thread_id,
    )
    return {"new_thread_id": new_thread_id, "copied": n}


@router.delete("/threads/{thread_id}")
async def delete_thread_endpoint(
    thread_id: str,
    request:   Request,
) -> dict:
    """Delete thread + all its turns; returns deleted row count (0 if not found); default sentinel is a no-op."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return {"deleted": 0}
    n = await delete_thread(request.app.state.pg_url, thread_id)
    return {"deleted": n}


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn_endpoint(
    turn_id: int,
    request: Request,
) -> dict:
    """Mark turn for early SSE exit and delete its PG row; idempotent (second call returns deleted=0)."""
    _CANCELLED_TURN_IDS.add(turn_id)
    try:
        n = await delete_turn(request.app.state.pg_url, turn_id)
    except Exception as e:
        logger.warning(
            f"[ycs:cancel] delete_turn({turn_id}) failed: "
            f"{type(e).__name__}: {e}"
        )
        n = 0
    return {"cancelled": True, "deleted": n}


@router.post("/search")
async def rag_search(
    payload: RAGSearchRequest,
    request: Request,
) -> dict:
    """Agentic RAG: cache check → history load → graph.ainvoke() → save turn → cache response."""
    if not payload.thread_id or payload.thread_id == "default":
        cached = await get_cached_response(
            request.app.state.redis_aio,
            payload.question,
            payload.force_mode,
        )
        if cached:
            cached["_from_cache"] = True
            return cached
    history = await get_history(
        request.app.state.pg_url, payload.thread_id,
    )
    graph = await build_graph_from_request(request)
    initial_state = {
        "question":             payload.question,
        "thread_id":            payload.thread_id or DEFAULT_THREAD_ID,
        "route":                "search",
        "contextualized":       False,
        "mode":                 "",
        "force_mode":           payload.force_mode or "",
        "conversation_history": history,
        "channel_ids":          payload.channel_ids or [],
        "generation":           "",
        "citations":            [],
        "grounded":             False,
        "retrieval_sources":    [],
        "retry_count":          0,
        "search_query":         payload.question,
        "sub_questions":        [],
        "sub_results":          [],
        "research_plan":        "",
        "confidence_score":     0.0,
    }
    config = {
        "configurable": {
            "thread_id":   payload.thread_id,
            "max_retries": payload.max_retries,
        },
        "recursion_limit": 100,
    }
    try:
        from infra.langfuse.sessions import session as _lf_session
        _sess_id  = payload.thread_id or "default"
        # 2026-09-15: every graph call runs under this thread key so the
        # per-conversation LLM-usage counter accumulates across nodes.
        # Sync + stream both set it; ingestion's extract_id path is
        # unchanged (different context altogether).
        _llm_set_thread(thread_id = _sess_id)
        _llm_set_node(node = None)  # nodes tag themselves before calling
        # 2026-09-16: pre-turn snapshot for the per-response usage badge
        # (see `_llm_diff_usage` below, mirroring the stream endpoint's
        # `_usage_before`/`_stamp_usage`).
        try:
            _usage_before = await _llm_read_counters(_sess_id)
        except Exception:
            _usage_before = None
        _user_id  = (payload.channel_ids or ["default"])[0]
        t0 = time.monotonic()
        with _lf_session(
            "ycs",
            session_id = _sess_id,
            user_id    = _user_id,
            channel_id = _user_id,
        ):
            with get_tracer().start_as_current_span(
                "ycs.ask.run",
                attributes = {
                    "coelho.langfuse.keep": True,
                    "coelho.langfuse.kind": "workflow_root",
                    "langfuse.trace.name": "ycs.ask.run",
                    "ycs.route":         "search",
                    "ycs.thread_id":     _sess_id,
                    "ycs.question":      payload.question[:200],
                    "ycs.force_mode":    payload.force_mode or "",
                    "ycs.channel_count": len(payload.channel_ids or []),
                    "langfuse.observation.metadata.workflow": "ycs_ask",
                },
            ):
                set_current_span_langfuse_io(input_data = _langfuse_ycs_input(
                    question = payload.question,
                    route = "search",
                    force_mode = payload.force_mode or "",
                    channel_ids = list(payload.channel_ids or []),
                    thread_id = _sess_id,
                ))
                set_current_span_langfuse_trace_metadata({
                    "pipeline": "ycs_ask",
                    "route": "search",
                    "thread_id": _sess_id,
                    "channel_id": _user_id,
                    "force_mode": payload.force_mode or "",
                })
                set_current_span_langfuse_observation_metadata({
                    "route": "search",
                    "channel_count": len(payload.channel_ids or []),
                })
                try:
                    _deadline = _ASK_DEADLINE_S.get(
                        (payload.force_mode or "").lower(),
                        _ASK_DEADLINE_DEFAULT_S,
                    )
                    try:
                        result = await asyncio.wait_for(
                            graph.ainvoke(initial_state, config = config),
                            timeout = _deadline,
                        )
                    except asyncio.TimeoutError:
                        # Global deadline hit — one bounded fallback pass
                        # instead of grinding until the client disconnects.
                        from domains.ycs.rag.standard.nodes.fallback_answer import (
                            fallback_answer as _deadline_fallback,
                        )
                        _fb = await _deadline_fallback(
                            {
                                "question":             payload.question,
                                "conversation_history": history,
                                "pre_grade_documents":  [],
                                "documents":            [],
                            },
                            request.app.state.llm,
                        )
                        result = {
                            "generation":        _fb.get("generation", ""),
                            "mode":              payload.force_mode or "standard",
                            "citations":         _fb.get("citations", []),
                            "grounded":          False,
                            "retrieval_sources": [],
                            "retry_count":       0,
                            "search_query":      payload.question,
                            "_deadline_hit":     True,
                        }
                except Exception as e:
                    set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                        status = "error",
                        answer = "",
                        mode = payload.force_mode or "unknown",
                        grounded = False,
                        citations = [],
                        error = str(e),
                    ))
                    raise
                set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                    status = "done",
                    answer = str(result.get("generation") or ""),
                    mode = str(result.get("mode") or payload.force_mode or "standard"),
                    grounded = bool(result.get("grounded")),
                    citations = list(result.get("citations") or []),
                    sub_questions = result.get("sub_questions"),
                    confidence_score = result.get("confidence_score"),
                ))
        record_ask_run(
            route = "search",
            mode = str(result.get("mode") or payload.force_mode or "standard"),
            outcome = "deadline" if result.get("_deadline_hit") else "done",
            grounded = bool(result.get("grounded")),
            duration_s = max(time.monotonic() - t0, 0.0),
            citation_count = len(result.get("citations") or []),
        )
    except Exception as e:
        record_ask_run(
            route = "search",
            mode = payload.force_mode or "unknown",
            outcome = "error",
            grounded = False,
        )
        raise HTTPException(
            status_code = 500,
            detail      = f"Agent error: {str(e)}",
        )
    mode = result.get("mode", "standard")
    usage: dict = {"total": {}, "by_model": {}}
    if _usage_before is not None:
        try:
            usage = _llm_diff_usage(
                _usage_before, await _llm_read_counters(_sess_id),
            )
        except Exception:
            pass
    response = {
        "answer":             result.get("generation", "No answer generated."),
        "mode":               mode,
        "citations":          result.get("citations", []),
        "grounded":           result.get("grounded", False),
        "retrieval_sources":  result.get("retrieval_sources", []),
        "retry_count":        result.get("retry_count", 0),
        "search_query":       result.get("search_query", payload.question),
        "usage":              usage,
    }
    if mode == "deep":
        response["sub_questions"]    = result.get("sub_questions", [])
        response["confidence_score"] = result.get("confidence_score", 0.0)
    await save_turn(
        request.app.state.pg_url,
        payload.thread_id,
        payload.question,
        response["answer"],
        mode,
    )
    if not payload.thread_id or payload.thread_id == "default":
        await cache_response(
            request.app.state.redis_aio,
            payload.question,
            response,
            # 2026-09-15: key MUST be the read key (force_mode as given,
            # possibly None) — writing the resolved mode meant auto
            # requests (read key: question-only) never hit their own
            # writes (question+resolved-mode). The payload's internal
            # "mode" field still carries the resolved mode.
            mode = payload.force_mode,
        )
    return response


@router.post("/search/stream")
async def rag_search_stream(
    payload: RAGSearchRequest,
    request: Request,
) -> StreamingResponse:
    """Streaming agentic RAG via SSE; one event per node completion; saves final answer to Postgres."""
    history = await get_history(
        request.app.state.pg_url, payload.thread_id,
    )
    # Thread scope is frozen to the first turn's channel_ids; enforce server-side so hand-crafted POSTs can't bypass.
    locked_scope = await get_thread_locked_scope(
        request.app.state.pg_url, payload.thread_id,
    )
    effective_channel_ids = (
        locked_scope if locked_scope is not None
        else (list(payload.channel_ids or []))
    )
    if (locked_scope is not None
        and set(locked_scope) != set(payload.channel_ids or [])):
        logger.info(
            f"[ycs:stream] thread {payload.thread_id} scope is locked to "
            f"{locked_scope!r}; ignoring caller-supplied "
            f"channel_ids={payload.channel_ids!r}"
        )
    graph = await build_graph_from_request(request)
    initial_state = {
        "question":             payload.question,
        "thread_id":            payload.thread_id or DEFAULT_THREAD_ID,
        "route":                "search_stream",
        "contextualized":       False,
        "mode":                 "",
        "force_mode":           payload.force_mode or "",
        "conversation_history": history,
        "channel_ids":          effective_channel_ids,
        "generation":           "",
        "citations":            [],
        "grounded":             False,
        "retrieval_sources":    [],
        "retry_count":          0,
        "search_query":         payload.question,
        "sub_questions":        list(payload.sub_questions or []),
        "sub_results":          [],
        "research_plan":        "",
        "confidence_score":     0.0,
    }
    config = {
        "configurable": {
            "thread_id":   payload.thread_id,
            "max_retries": payload.max_retries,
        },
        "recursion_limit": 100,
    }
    # 2026-09-15: stream-side answer cache (parity with sync `/search`).
    # Stateless turns only (same condition as sync), never a caller-
    # planned request. Hit → replay the cached answer as `generate` +
    # `end` frames (the frontend renders `generation` + `citations`
    # from any node event); the turn is still persisted to Postgres so
    # history stays consistent.
    if (
        (not payload.thread_id or payload.thread_id == "default")
        and not (payload.sub_questions or [])
    ):
        _hit = await get_cached_response(
            request.app.state.redis_aio,
            payload.question,
            payload.force_mode,
        )
        if _hit and _hit.get("answer"):
            async def _replay_cached():
                _hit_turn_id: int | None = None
                try:
                    _hit_turn_id = await insert_turn(
                        request.app.state.pg_url,
                        payload.thread_id,
                        payload.question,
                    )
                except Exception:
                    pass
                yield (
                    "data: "
                    + json.dumps({"node": "_meta", "turn_id": _hit_turn_id})
                    + "\n\n"
                )
                yield (
                    "data: "
                    + json.dumps({
                        "node":      "generate",
                        "generation": _hit.get("answer", ""),
                        "mode":       _hit.get("mode", "standard"),
                        "grounded":   _hit.get("grounded", True),
                        "citations":  _hit.get("citations", []),
                    })
                    + "\n\n"
                )
                if _hit_turn_id is not None:
                    try:
                        await update_turn_answer(
                            request.app.state.pg_url,
                            _hit_turn_id,
                            _hit.get("answer", ""),
                            _hit.get("mode") or "standard",
                            thinking_state = {
                                "duration_ms": 0,
                                "cached":      True,
                            },
                        )
                    except Exception:
                        pass
                yield (
                    "data: "
                    + json.dumps({
                        "node":        "end",
                        "status":      "complete",
                        "duration_ms": 0,
                    })
                    + "\n\n"
                )

            return StreamingResponse(
                _replay_cached(), media_type = "text/event-stream",
            )

    turn_id: int | None = None
    try:
        turn_id = await insert_turn(
            request.app.state.pg_url,
            payload.thread_id,
            payload.question,
        )
    except Exception as e:
        logger.warning(
            f"[ycs:stream] turn placeholder insert failed: "
            f"{type(e).__name__}: {e}"
        )

    _STAGE_ORDER = ["retrieve", "grade", "generate", "verify"]
    _NODE_STAGE_ACTION: dict[str, tuple[str, str]] = {
        "contextualize":       ("retrieve", "Resolving prior context"),
        "classify_query":      ("retrieve", "Classifying intent"),
        "retrieve":            ("retrieve", "Searching transcripts"),
        "rewrite_query":       ("retrieve", "Refining query"),
        "plan_research":       ("retrieve", "Planning sub-questions"),
        "run_subagent":        ("retrieve", "Researching sub-question"),
        "grade_documents":     ("grade",    "Grading documents"),
        "direct_answer":       ("generate", "Composing answer"),
        "run_standard":        ("generate", "Running standard pipeline"),
        "generate":            ("generate", "Writing answer"),
        "synthesize":          ("generate", "Synthesizing findings"),
        "check_hallucination": ("verify",   "Verifying grounding"),
        "format_citations":    ("verify",   "Formatting citations"),
        "critic":              ("verify",   "Assessing confidence"),
    }

    def _thinking_apply(state: dict, node_name: str, update: dict) -> dict:
        sa = _NODE_STAGE_ACTION.get(node_name)
        if sa:
            stage, action = sa
            stage_idx = _STAGE_ORDER.index(stage)
            stages = state.setdefault("stages", {})
            for i, s in enumerate(_STAGE_ORDER):
                cur = stages.setdefault(s, {"status": "queued", "action": ""})
                if i < stage_idx:
                    cur["status"] = "done"
                    cur["action"] = ""
                elif i == stage_idx:
                    cur["status"] = "active"
                    cur["action"] = action
        if update.get("mode"):
            state["mode"] = update["mode"]
        if update.get("sub_questions"):
            deep = state.get("deep")
            if not isinstance(deep, dict):
                deep = {
                    "research_plan": "",
                    "sub_questions": [],
                    "confidence_score": None,
                }
                state["deep"] = deep
            deep["research_plan"] = (
                update.get("research_plan")
                or deep.get("research_plan", "")
            )
            deep["sub_questions"] = [
                {"question": q, "status": "queued", "answer_preview": ""}
                for q in update["sub_questions"]
            ]
        if update.get("sub_results"):
            deep = state.get("deep")
            if isinstance(deep, dict) and isinstance(deep.get("sub_questions"), list):
                # 2026-09-16: mark EVERY item, not just [-1] — a bulk
                # `run_subagents` return carries N results in one update,
                # and custom per-finish events carry one each; either way
                # every listed question must flip, or cards silently stay
                # queued (observed live: only the last card flipped).
                for latest in update["sub_results"]:
                    if not isinstance(latest, dict):
                        continue
                    target = latest.get("sub_question", "") or ""
                    full_answer = latest.get("answer", "") or ""
                    if not full_answer.strip():
                        full_answer = (
                            "_(this sub-question completed but "
                            "produced no answer.)_"
                        )
                    error_kind = latest.get("error_kind") or ""
                    for sq in deep["sub_questions"]:
                        if sq.get("question") == target:
                            sq["status"] = "done"
                            sq["answer"] = full_answer
                            sq["error_kind"] = error_kind
                            sq["answer_preview"] = full_answer[:200]
                            break
                # Piggyback on every sub_results event to keep the retrieve label updated (N/M done).
                stages = state.setdefault("stages", {})
                rs = stages.setdefault(
                    "retrieve", {"status": "active", "action": ""},
                )
                done = sum(
                    1 for sq in deep["sub_questions"]
                    if sq.get("status") == "done"
                )
                total = len(deep["sub_questions"])
                rs["status"] = "active"
                rs["action"] = (
                    f"Researching sub-questions ({done}/{total} done)"
                )
        if update.get("confidence_score") is not None:
            deep = state.get("deep")
            if isinstance(deep, dict):
                deep["confidence_score"] = update["confidence_score"]
        return state

    def _thinking_finalize(state: dict) -> dict:
        """End-of-stream: all four stages → done, no active stage."""
        stages = state.setdefault("stages", {})
        for s in _STAGE_ORDER:
            cur = stages.setdefault(s, {"status": "done", "action": ""})
            cur["status"] = "done"
            cur["action"] = ""
        return state

    def _stamp_duration(state: dict, t_start: float) -> int:
        """Write elapsed ms into state["duration_ms"]; final stamp wins on retries."""
        ms = int((time.monotonic() - t_start) * 1000)
        state["duration_ms"] = ms
        return ms

    def _stamp_citations(state: dict, citations: list) -> None:
        """Fold citations into state so they survive in the JSONB column after SSE closes."""
        if isinstance(citations, list) and citations:
            state["citations"] = citations

    async def _stamp_usage(
        state: dict, thread_id: str, before: dict | None,
    ) -> None:
        """Diff live counters against the `before` snapshot taken at
        turn start and write the delta into state["usage"] — same
        JSONB-piggyback pattern as `_stamp_duration`/`_stamp_citations`
        so it survives in `thinking_state` with zero schema change and
        rides the SSE `end` frame for the live badge. Best-effort: a
        Redis hiccup here must never fail an otherwise-successful turn."""
        if before is None:
            return
        try:
            after = await _llm_read_counters(thread_id)
            state["usage"] = _llm_diff_usage(before, after)
        except Exception as e:
            logger.warning(
                f"[ycs:stream] usage stamp failed thread_id={thread_id}: "
                f"{type(e).__name__}: {e}"
            )

    async def event_generator():
        from infra.langfuse.sessions import session as _lf_session
        _sess_id = payload.thread_id or DEFAULT_THREAD_ID
        _user_id = (effective_channel_ids or ["default"])[0]
        # 2026-09-15: same thread-tagging as sync `/search` so the
        # conversation-level usage counter captures this stream too.
        _llm_set_thread(thread_id = _sess_id)
        _llm_set_node(node = None)
        # 2026-09-16: pre-turn snapshot for the per-response usage badge
        # (`_stamp_usage` diffs against this at every terminal branch).
        # `None` on failure — `_stamp_usage` no-ops rather than stamping
        # a misleading delta off a missing baseline.
        try:
            _usage_before = await _llm_read_counters(_sess_id)
        except Exception:
            _usage_before = None
        _session_cm = _lf_session(
            "ycs",
            session_id = _sess_id,
            user_id    = _user_id,
            channel_id = _user_id,
        )
        _session_cm.__enter__()
        _span_cm = get_tracer().start_as_current_span(
            "ycs.ask.stream.run",
            attributes = {
                "coelho.langfuse.keep": True,
                "coelho.langfuse.kind": "workflow_root",
                "langfuse.trace.name": "ycs.ask.stream.run",
                "ycs.route":         "search_stream",
                "ycs.thread_id":     _sess_id,
                "ycs.question":      payload.question[:200],
                "ycs.force_mode":    payload.force_mode or "",
                "ycs.channel_count": len(effective_channel_ids or []),
                "langfuse.observation.metadata.workflow": "ycs_ask",
            },
        )
        _span_cm.__enter__()
        set_current_span_langfuse_io(input_data = _langfuse_ycs_input(
            question = payload.question,
            route = "search_stream",
            force_mode = payload.force_mode or "",
            channel_ids = list(effective_channel_ids or []),
            thread_id = _sess_id,
        ))
        set_current_span_langfuse_trace_metadata({
            "pipeline": "ycs_ask",
            "route": "search_stream",
            "thread_id": _sess_id,
            "channel_id": _user_id,
            "force_mode": payload.force_mode or "",
        })
        set_current_span_langfuse_observation_metadata({
            "route": "search_stream",
            "channel_count": len(effective_channel_ids or []),
        })
        last_generation = ""
        last_mode       = ""
        last_persisted  = ""
        last_citations: list = []
        last_grounded = False
        t_run_start     = time.monotonic()
        last_persist_t  = t_run_start
        first_persist_done = False
        thinking_state: dict = {
            "stages":      {
                "retrieve": {
                    "status": "active",
                    "action": (
                        "Resolving prior context"
                        if history else
                        "Classifying intent"
                    ),
                },
                "grade":    {"status": "queued", "action": ""},
                "generate": {"status": "queued", "action": ""},
                "verify":   {"status": "queued", "action": ""},
            },
            "mode":        "",
            "channel_ids": list(effective_channel_ids),
        }
        cancelled = False
        stalled   = False
        deadline_hit = False
        # 2026-09-15 stream-side global deadline (parity with sync
        # `/search`'s per-mode table): the silence watchdog below only
        # catches a hung producer — a grinding producer (events flowing,
        # no progress) needs a wall-clock bound too.
        _stream_deadline_s = _ASK_DEADLINE_S.get(
            (payload.force_mode or "").lower(), _ASK_DEADLINE_DEFAULT_S,
        )
        # Heartbeat in the main loop, not a background task — CancelledError would silently kill a background coroutine.
        # DEEP sub-agents yield zero parent events for 5-15 min; ticks prevent false stall detection.
        hb_seq                  = 0
        heartbeats_since_event  = 0
        _MAX_HEARTBEATS_BEFORE_WATCHDOG = int(
            _LANGGRAPH_WATCHDOG_S / _STREAM_PERSIST_INTERVAL_S
        )
        try:
            if turn_id is not None:
                yield (
                    "data: "
                    + json.dumps({"node": "_meta", "turn_id": turn_id})
                    + "\n\n"
                )
            # Producer/consumer split: wait_for on the queue, not on __anext__ directly —
            # wait_for cancels the inner coroutine on timeout, which closes the async generator early.
            event_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
            producer_tasks: list[asyncio.Task] = []
            saw_graph_event = False
            using_invoke_fallback = False

            async def _producer_stream():
                try:
                    with _lf_session(
                        "ycs",
                        session_id = _sess_id,
                        user_id    = _user_id,
                        channel_id = _user_id,
                    ):
                        # 2026-09-16: ["updates","custom"] — "updates" carries
                        # the per-node patches as before; "custom" carries
                        # per-sub-question live finishes from
                        # `run_subagents_bounded`'s stream writer (one card
                        # flip per finish, while siblings still run). With
                        # a mode list, astream yields (mode, payload)
                        # tuples instead of bare update dicts.
                        async for ev in graph.astream(
                            initial_state,
                            config      = config,
                            stream_mode = ["updates", "custom"],
                        ):
                            if (
                                isinstance(ev, tuple)
                                and len(ev) == 2
                                and ev[0] in ("updates", "custom")
                            ):
                                _mode, _payload = ev
                                if _mode == "custom":
                                    await event_queue.put(("custom", _payload))
                                else:
                                    await event_queue.put(("event", _payload))
                            else:
                                await event_queue.put(("event", ev))
                        await event_queue.put(("done", None))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    try:
                        await event_queue.put(("error", e))
                    except Exception:
                        pass

            async def _producer_invoke_fallback():
                try:
                    with _lf_session(
                        "ycs",
                        session_id = _sess_id,
                        user_id    = _user_id,
                        channel_id = _user_id,
                    ):
                        result = await graph.ainvoke(
                            initial_state,
                            config = config,
                        )
                    for ev in _result_to_graph_updates(result):
                        await event_queue.put(("event", ev))
                    await event_queue.put(("done", None))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    try:
                        await event_queue.put(("error", e))
                    except Exception:
                        pass

            producer_task = asyncio.create_task(_producer_stream())
            producer_tasks.append(producer_task)
            try:
                while True:
                    if turn_id is not None and turn_id in _CANCELLED_TURN_IDS:
                        cancelled = True
                        break
                    if await request.is_disconnected():
                        cancelled = True
                        break
                    if (
                        time.monotonic() - t_run_start
                    ) > _stream_deadline_s:
                        logger.warning(
                            f"[ycs:stream] deadline: "
                            f"{int(_stream_deadline_s)}s wall exceeded on "
                            f"turn_id={turn_id} — finalizing with partial "
                            f"content instead of grinding."
                        )
                        for task in producer_tasks:
                            task.cancel()
                        deadline_hit = True
                        break
                    try:
                        kind, queue_payload = await asyncio.wait_for(
                            event_queue.get(),
                            timeout = _STREAM_PERSIST_INTERVAL_S,
                        )
                    except asyncio.TimeoutError:
                        heartbeats_since_event += 1
                        if heartbeats_since_event >= _MAX_HEARTBEATS_BEFORE_WATCHDOG:
                            logger.warning(
                                f"[ycs:stream] watchdog: no LangGraph "
                                f"event for {int(_LANGGRAPH_WATCHDOG_S)}s "
                                f"on turn_id={turn_id} — assuming a "
                                f"node hung silently inside the graph. "
                                f"Bailing."
                            )
                            stalled = True
                            break
                        if turn_id is not None:
                            hb_seq += 1
                            try:
                                snap = dict(thinking_state)
                                snap["_seq"] = hb_seq
                                await asyncio.wait_for(
                                    update_turn_answer(
                                        request.app.state.pg_url,
                                        turn_id, last_generation, last_mode,
                                        thinking_state = snap,
                                    ),
                                    timeout = _PERSIST_TIMEOUT_S,
                                )
                            except (asyncio.TimeoutError, Exception) as e:
                                logger.warning(
                                    f"[ycs:stream] heartbeat persist "
                                    f"failed: {type(e).__name__}: {e}"
                                )
                        if (
                            not saw_graph_event
                            and not using_invoke_fallback
                            and heartbeats_since_event
                            >= _ASTREAM_BOOTSTRAP_FALLBACK_TICKS
                        ):
                            using_invoke_fallback = True
                            logger.warning(
                                "[ycs:stream] no LangGraph stream event "
                                f"after {int(_ASTREAM_BOOTSTRAP_FALLBACK_S)}s "
                                f"on turn_id={turn_id}; cancelling "
                                "`astream()` producer and falling back "
                                "to `ainvoke()`"
                            )
                            producer_task.cancel()
                            try:
                                await asyncio.wait_for(
                                    producer_task, timeout = 5.0,
                                )
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                            except Exception as e:
                                logger.warning(
                                    "[ycs:stream] bootstrap fallback "
                                    f"cancel wait failed: "
                                    f"{type(e).__name__}: {e}"
                                )
                            producer_task = asyncio.create_task(
                                _producer_invoke_fallback(),
                            )
                            producer_tasks.append(producer_task)
                        # SSE comment frame keeps TCP alive during silent DEEP sub-agent runs (5-10+ min).
                        yield f": heartbeat {hb_seq}\n\n"
                        continue
                    if kind == "done":
                        break
                    if kind == "error":
                        raise queue_payload  # propagate to outer except
                    if kind == "custom":
                        # 2026-09-16: live per-finish from
                        # `run_subagents_bounded`'s stream writer —
                        # normalize to the same {"run_subagent":
                        # {"sub_results": [...]}} shape the updates path
                        # already handles, so thinking/persist/serialize
                        # stay in one place. Malformed customs are
                        # skipped, never fatal.
                        _item = (
                            queue_payload.get("sub_result")
                            if isinstance(queue_payload, dict) else None
                        )
                        if not isinstance(_item, dict):
                            continue
                        queue_payload = {"run_subagent": {"sub_results": [_item]}}
                        kind = "event"
                    # kind == "event"
                    event = queue_payload
                    saw_graph_event = True
                    heartbeats_since_event = 0
                    for node_name, update in event.items():
                        if not isinstance(update, dict):
                            yield f"data: {json.dumps({'node': node_name})}\n\n"
                            continue
                        if "generation" in update and update["generation"]:
                            last_generation = update["generation"]
                        if "mode" in update and update["mode"]:
                            last_mode = update["mode"]
                        if isinstance(update.get("citations"), list):
                            last_citations = update["citations"]
                        if update.get("grounded") is not None:
                            last_grounded = bool(update.get("grounded"))
                        thinking_state = _thinking_apply(
                            thinking_state, node_name, update,
                        )
                        serializable_update = _serialize_update(
                            node_name, update,
                        )
                        yield (
                            f"data: {json.dumps(serializable_update)}\n\n"
                        )
                        now_t = time.monotonic()
                        should_persist = turn_id is not None and (
                            not first_persist_done
                            or now_t - last_persist_t >= _STREAM_PERSIST_INTERVAL_S
                        )
                        if should_persist:
                            try:
                                await asyncio.wait_for(
                                    update_turn_answer(
                                        request.app.state.pg_url,
                                        turn_id, last_generation, last_mode,
                                        thinking_state = thinking_state,
                                    ),
                                    timeout = _PERSIST_TIMEOUT_S,
                                )
                                last_persisted = last_generation
                                last_persist_t = now_t
                                first_persist_done = True
                            except (asyncio.TimeoutError, Exception) as e:
                                logger.warning(
                                    f"[ycs:stream] incremental persist "
                                    f"failed: {type(e).__name__}: {e}"
                                )
                if cancelled:
                    logger.info(
                        f"[ycs:stream] cancelled mid-flight turn_id={turn_id}"
                    )
                    record_ask_run(
                        route = "search_stream",
                        mode = last_mode or payload.force_mode or "unknown",
                        outcome = "cancelled",
                        grounded = last_grounded,
                        duration_s = max(time.monotonic() - t_run_start, 0.0),
                        citation_count = len(last_citations),
                    )
                    set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                        status = "cancelled",
                        answer = last_generation,
                        mode = last_mode or payload.force_mode or "unknown",
                        grounded = last_grounded,
                        citations = last_citations,
                    ))
                    yield (
                        "data: "
                        + json.dumps({"node": "end", "status": "cancelled"})
                        + "\n\n"
                    )
                elif deadline_hit:
                    # 2026-09-15/16: global deadline expired. Run ONE
                    # bounded fallback pass (general knowledge + whatever
                    # was asked) when nothing was generated yet — mirrors
                    # the sync `/search` deadline path — instead of a
                    # canned "(partial...)" placeholder over real work.
                    # (Fixed 2026-09-16: this used to be shadowed by a
                    # second, unreachable `elif deadline_hit:` further
                    # down that actually ran this fallback call — the
                    # live branch only ever inserted the placeholder
                    # text below. Merged into one branch.)
                    if not last_generation:
                        from domains.ycs.rag.standard.nodes.fallback_answer import (
                            fallback_answer as _deadline_fallback,
                        )
                        try:
                            _fb = await _deadline_fallback(
                                {
                                    "question":             payload.question,
                                    "conversation_history": history,
                                    "pre_grade_documents":  [],
                                    "documents":            [],
                                },
                                request.app.state.llm,
                            )
                            last_generation = _fb.get("generation", "")
                            last_citations = _fb.get("citations", [])
                            last_mode = payload.force_mode or "standard"
                            last_grounded = False
                            yield (
                                "data: "
                                + json.dumps({
                                    "node":       "fallback_answer",
                                    "generation": last_generation,
                                    "citations":  last_citations,
                                    "grounded":   last_grounded,
                                })
                                + "\n\n"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ycs:stream] deadline fallback failed: "
                                f"{type(e).__name__}: {e}"
                            )
                            last_generation = (
                                f"(no response — pipeline exceeded "
                                f"{int(_stream_deadline_s)}s deadline. Please retry.)"
                            )
                    if turn_id is not None:
                        try:
                            thinking_state = _thinking_finalize(thinking_state)
                            _stamp_duration(thinking_state, t_run_start)
                            await _stamp_usage(
                                thinking_state, _sess_id, _usage_before,
                            )
                            _stamp_citations(thinking_state, last_citations)
                            await asyncio.wait_for(
                                update_turn_answer(
                                    request.app.state.pg_url,
                                    turn_id, last_generation, last_mode,
                                    thinking_state = thinking_state,
                                ),
                                timeout = _PERSIST_TIMEOUT_S,
                            )
                        except (asyncio.TimeoutError, Exception) as e:
                            logger.warning(
                                f"[ycs:stream] deadline finalize failed: "
                                f"{type(e).__name__}: {e}"
                            )
                    record_ask_run(
                        route = "search_stream",
                        mode = last_mode or payload.force_mode or "unknown",
                        outcome = "deadline",
                        grounded = last_grounded,
                        duration_s = max(time.monotonic() - t_run_start, 0.0),
                        citation_count = len(last_citations),
                    )
                    set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                        status = "done",
                        answer = last_generation,
                        mode = last_mode or payload.force_mode or "unknown",
                        grounded = last_grounded,
                        citations = last_citations,
                        sub_questions = (
                            (thinking_state.get("deep") or {}).get("sub_questions")
                            if isinstance(thinking_state.get("deep"), dict) else
                            None
                        ),
                        confidence_score = (
                            (thinking_state.get("deep") or {}).get("confidence_score")
                            if isinstance(thinking_state.get("deep"), dict) else
                            None
                        ),
                    ))
                    yield (
                        "data: "
                        + json.dumps({
                            "node":        "end",
                            "status":      "complete",
                            "duration_ms": thinking_state.get("duration_ms"),
                            "usage":       thinking_state.get("usage"),
                        })
                        + "\n\n"
                    )
                elif stalled:
                    sentinel = (
                        "(no response — pipeline stalled after "
                        f"{int(_LANGGRAPH_WATCHDOG_S / 60)} min "
                        "of silence. A node hung without a "
                        "timeout; expand Thinking to see the "
                        "last reachable step.)"
                    )
                    if turn_id is not None:
                        try:
                            thinking_state = _thinking_finalize(thinking_state)
                            _stamp_duration(thinking_state, t_run_start)
                            await _stamp_usage(
                                thinking_state, _sess_id, _usage_before,
                            )
                            _stamp_citations(thinking_state, last_citations)
                            await asyncio.wait_for(
                                update_turn_answer(
                                    request.app.state.pg_url,
                                    turn_id, sentinel, last_mode,
                                    thinking_state = thinking_state,
                                ),
                                timeout = _PERSIST_TIMEOUT_S,
                            )
                        except (asyncio.TimeoutError, Exception) as e:
                            logger.warning(
                                f"[ycs:stream] watchdog finalize "
                                f"failed: {type(e).__name__}: {e}"
                            )
                    record_ask_run(
                        route = "search_stream",
                        mode = last_mode or payload.force_mode or "unknown",
                        outcome = "stalled",
                        grounded = last_grounded,
                        duration_s = max(time.monotonic() - t_run_start, 0.0),
                        citation_count = len(last_citations),
                    )
                    set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                        status = "stalled",
                        answer = sentinel,
                        mode = last_mode or payload.force_mode or "unknown",
                        grounded = last_grounded,
                        citations = last_citations,
                    ))
                    yield (
                        "data: "
                        + json.dumps({
                            "node":        "end",
                            "status":      "stalled",
                            "duration_ms": thinking_state.get("duration_ms"),
                            "usage":       thinking_state.get("usage"),
                        })
                        + "\n\n"
                    )
                else:
                    if turn_id is not None and last_generation:
                        try:
                            thinking_state = _thinking_finalize(thinking_state)
                            _stamp_duration(thinking_state, t_run_start)
                            await _stamp_usage(
                                thinking_state, _sess_id, _usage_before,
                            )
                            _stamp_citations(thinking_state, last_citations)
                            await asyncio.wait_for(
                                update_turn_answer(
                                    request.app.state.pg_url,
                                    turn_id, last_generation, last_mode,
                                    thinking_state = thinking_state,
                                ),
                                timeout = _PERSIST_TIMEOUT_S,
                            )
                        except (asyncio.TimeoutError, Exception) as e:
                            logger.warning(
                                f"[ycs:stream] final persist failed: "
                                f"{type(e).__name__}: {e}"
                            )
                    elif turn_id is not None and not last_generation:
                        try:
                            thinking_state = _thinking_finalize(thinking_state)
                            _stamp_duration(thinking_state, t_run_start)
                            await _stamp_usage(
                                thinking_state, _sess_id, _usage_before,
                            )
                            _stamp_citations(thinking_state, last_citations)
                            await asyncio.wait_for(
                                update_turn_answer(
                                    request.app.state.pg_url,
                                    turn_id,
                                    "(no response — see Thinking for pipeline status)",
                                    last_mode,
                                    thinking_state = thinking_state,
                                ),
                                timeout = _PERSIST_TIMEOUT_S,
                            )
                        except (asyncio.TimeoutError, Exception) as e:
                            logger.warning(
                                f"[ycs:stream] no-generation finalize "
                                f"failed: {type(e).__name__}: {e}"
                            )
                    record_ask_run(
                        route = "search_stream",
                        mode = last_mode or payload.force_mode or "unknown",
                        outcome = "done",
                        grounded = last_grounded,
                        duration_s = max(time.monotonic() - t_run_start, 0.0),
                        citation_count = len(last_citations),
                    )
                    # 2026-09-15: stream-side cache write (read lives at
                    # the top of this endpoint + in sync `/search`). Same
                    # stateless-only rule; key uses force_mode as given
                    # (see the sync-write comment for why resolved mode
                    # must NOT be the key).
                    if (
                        (not payload.thread_id or payload.thread_id == "default")
                        and not (payload.sub_questions or [])
                        and last_generation
                    ):
                        await cache_response(
                            request.app.state.redis_aio,
                            payload.question,
                            {
                                "answer":            last_generation,
                                "mode":              last_mode or "standard",
                                "citations":         last_citations,
                                "grounded":          last_grounded,
                                "retrieval_sources": [],
                            },
                            mode = payload.force_mode,
                        )
                    final_answer = (
                        last_generation
                        if last_generation else
                        "(no response — see Thinking for pipeline status)"
                    )
                    set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                        status = "done",
                        answer = final_answer,
                        mode = last_mode or payload.force_mode or "unknown",
                        grounded = last_grounded,
                        citations = last_citations,
                        sub_questions = (
                            (thinking_state.get("deep") or {}).get("sub_questions")
                            if isinstance(thinking_state.get("deep"), dict) else
                            None
                        ),
                        confidence_score = (
                            (thinking_state.get("deep") or {}).get("confidence_score")
                            if isinstance(thinking_state.get("deep"), dict) else
                            None
                        ),
                    ))
                    yield (
                        "data: "
                        + json.dumps({
                            "node":        "end",
                            "status":      "complete",
                            "duration_ms": thinking_state.get("duration_ms"),
                            "usage":       thinking_state.get("usage"),
                        })
                        + "\n\n"
                    )
            finally:
                for task in producer_tasks:
                    task.cancel()
                for task in producer_tasks:
                    try:
                        await task
                    except BaseException:
                        pass
        except asyncio.CancelledError:
            # await re-raises immediately in CancelledError context — detached task lets PG write complete after generator returns.
            logger.info(
                f"[ycs:stream] cancelled (client disconnect) "
                f"turn_id={turn_id} — scheduling sentinel persist and re-raising"
            )
            record_ask_run(
                route = "search_stream",
                mode = last_mode or payload.force_mode or "unknown",
                outcome = "client_disconnect",
                grounded = last_grounded,
                duration_s = max(time.monotonic() - t_run_start, 0.0),
                citation_count = len(last_citations),
            )
            set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                status = "client_disconnect",
                answer = last_generation,
                mode = last_mode or payload.force_mode or "unknown",
                grounded = last_grounded,
                citations = last_citations,
            ))
            if turn_id is not None:
                thinking_state_snapshot = _thinking_finalize(thinking_state)
                _stamp_duration(thinking_state_snapshot, t_run_start)
                _stamp_citations(thinking_state_snapshot, last_citations)
                answer_text = (
                    last_generation
                    or "(stream interrupted — the SSE connection "
                       "dropped before the answer landed (browser "
                       "refresh, network blip, or backend restart). "
                       "Re-ask the question to start fresh.)"
                )
                async def _persist_cancellation_sentinel(
                    pg_url:        str,
                    tid:           int,
                    answer:        str,
                    mode:          str,
                    state_snapshot: dict,
                ):
                    try:
                        await asyncio.wait_for(
                            update_turn_answer(
                                pg_url, tid, answer, mode,
                                thinking_state = state_snapshot,
                            ),
                            timeout = 5.0,
                        )
                    except BaseException as exc:
                        logger.warning(
                            f"[ycs:stream] cancellation sentinel "
                            f"persist failed for turn_id={tid}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                asyncio.create_task(
                    _persist_cancellation_sentinel(
                        request.app.state.pg_url,
                        turn_id, answer_text, last_mode,
                        thinking_state_snapshot,
                    )
                )
            raise
        except Exception as e:
            if turn_id is not None:
                try:
                    if last_generation:
                        _stamp_duration(thinking_state, t_run_start)
                        _stamp_citations(thinking_state, last_citations)
                        await _stamp_usage(
                            thinking_state, _sess_id, _usage_before,
                        )
                        await update_turn_answer(
                            request.app.state.pg_url,
                            turn_id, last_generation, last_mode,
                            thinking_state = thinking_state,
                        )
                    else:
                        await delete_turn(
                            request.app.state.pg_url, turn_id,
                        )
                except Exception:
                    pass
            record_ask_run(
                route = "search_stream",
                mode = last_mode or payload.force_mode or "unknown",
                outcome = "error",
                grounded = last_grounded,
                duration_s = max(time.monotonic() - t_run_start, 0.0),
                citation_count = len(last_citations),
            )
            set_current_span_langfuse_io(output_data = _langfuse_ycs_output(
                status = "error",
                answer = last_generation,
                mode = last_mode or payload.force_mode or "unknown",
                grounded = last_grounded,
                citations = last_citations,
                error = str(e),
            ))
            yield (
                "data: "
                + json.dumps({
                    "node":        "error",
                    "error":       str(e),
                    "duration_ms": thinking_state.get("duration_ms"),
                    "usage":       thinking_state.get("usage"),
                })
                + "\n\n"
            )
        finally:
            try:
                _span_cm.__exit__(None, None, None)
            except Exception:
                pass
            try:
                _session_cm.__exit__(None, None, None)
            except Exception:
                pass
            if turn_id is not None:
                _CANCELLED_TURN_IDS.discard(turn_id)

    return StreamingResponse(
        event_generator(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control": "no-cache",
            "Connection":    "keep-alive",
        },
    )


async def _raise_if_embedding_migration_needed() -> None:
    """2026-09-15: shares the SAME check `api/v1/ycs/content/router.py`
    uses before every Videos-tab dispatch. This endpoint (the Source
    tab's "Continue to Qdrant" follow-up, `static/js/ycs/ingest.js`) and
    `/pipeline` below (when `include_qdrant`) had NO gate at all before
    this — either can write a video's vectors under a DIFFERENT model
    than everything already in the active collection, exactly the
    silent-corpus-fragmentation failure mode the gate exists to
    prevent. See `domains.ycs.embedding_migration.check_migration_needed_now`
    for the actual check."""
    from domains.ycs.embedding_migration import check_migration_needed_now
    mismatch = await check_migration_needed_now()
    if mismatch is not None:
        raise HTTPException(
            status_code = 423,
            detail = {
                "error": "embedding_migration_required",
                "message": (
                    f"The configured embedding model changed from "
                    f"{mismatch['from_model']!r} to {mismatch['to_model']!r} "
                    f"since the last ingestion. Start a migration "
                    f"(POST /api/v1/ycs/content/embedding-migration/start) "
                    f"before ingesting more videos."
                ),
                **mismatch,
            },
        )


@router.post("/ingest/qdrant")
async def ingest_to_qdrant(payload: IngestRequest) -> dict:
    """Queue ES transcripts → Qdrant ingestion (Celery)."""
    await _raise_if_embedding_migration_needed()
    from domains.ycs.qdrant_task.task import ingest_to_qdrant as ingest_task
    task = ingest_task.delay(
        payload.video_ids,
        payload.chunk_size,
        payload.chunk_overlap,
    )
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


@router.post("/ingest/neo4j")
async def ingest_to_neo4j(payload: GraphIngestRequest) -> dict:
    """Queue entity extraction → Neo4j (Celery); 1 LLM call per transcript."""
    from domains.ycs.neo4j_task.task import ingest_to_neo4j as graph_task
    task = graph_task.delay(payload.video_ids, payload.batch_size)
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


@router.get("/graph/stats")
async def graph_stats(request: Request) -> dict:
    """Get Neo4j node/relationship counts."""
    try:
        stats = await get_graph_stats(request.app.state.neo4j_graph)
        return stats
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail      = f"Graph stats error: {str(e)}",
        )


@router.post("/pipeline")
async def full_pipeline(payload: PipelineRequest) -> dict:
    """Queue full Celery chain: extract → Qdrant → Neo4j → cache."""
    if payload.include_qdrant:
        await _raise_if_embedding_migration_needed()
    from domains.ycs.pipeline_task.task import full_channel_pipeline
    task = full_channel_pipeline.delay(
        payload.channel_id,
        payload.max_results,
        payload.include_transcription,
        payload.include_qdrant,
        payload.include_graph,
    )
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }
