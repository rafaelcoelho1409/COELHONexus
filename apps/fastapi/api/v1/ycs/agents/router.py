"""ycs/agents — agentic RAG router: BYOK config, ask (sync+stream), ingest, graph stats, pipeline."""
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

from domains.llm.credentials           import resolve_key
from domains.llm.rotator.benchmarks    import rank_for_step
from domains.llm.rotator.chain.domain  import is_non_chat_model
from domains.llm.rotator.discovery     import (
    PROVIDERS,
    list_all_alive_models,
)

from .build import _serialize_update, build_graph_from_request
from .byok import CONFIG_REDIS_KEY, get_byok_config, ping_byok
from .schemas import (
    GraphIngestRequest,
    IngestRequest,
    LLMConfig,
    PipelineRequest,
    RAGSearchRequest,
)


# Human-readable provider labels for the BYOK dropdown; decoupled from rotator identifiers.
_PROVIDER_LABELS: dict[str, str] = {
    "nim":        "NVIDIA NIM",
    "groq":       "Groq",
    "cerebras":   "Cerebras",
    "gemini":     "Google Gemini",
    "mistral":    "Mistral",
    "deepseek":   "DeepSeek",
    "sambanova":  "SambaNova",
    "openai":     "OpenAI",
    "anthropic":  "Anthropic",
    "openrouter": "OpenRouter",
    "ollama":     "Ollama (local)",
}


router = APIRouter()
logger = logging.getLogger(__name__)

# Min gap between incremental Postgres writes; smaller = more live, larger = fewer PG round-trips.
_STREAM_PERSIST_INTERVAL_S = 2.5

# If no astream() event arrives within this window at bootstrap, fall back to ainvoke()
# (local k3d hangs before the first stream event while ainvoke completes normally).
_ASTREAM_BOOTSTRAP_FALLBACK_S = 15.0
_ASTREAM_BOOTSTRAP_FALLBACK_TICKS = max(
    1, int(_ASTREAM_BOOTSTRAP_FALLBACK_S / _STREAM_PERSIST_INTERVAL_S),
)

# 15 min ≈ 3× the slowest DEEP sub-agent (recursion_limit=12, cap=3).
_LANGGRAPH_WATCHDOG_S = 15 * 60.0

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


@router.get("/config")
async def get_agents_config(request: Request) -> dict:
    """Return the persisted BYOK config (api_key redacted) so the UI
    can populate the form on page load. Empty dict if nothing is set."""
    config = await get_byok_config(request.app.state.redis_aio)
    if not config:
        return {"config": {}, "has_api_key": False}
    safe = {k: v for k, v in config.items() if k != "api_key"}
    return {"config": safe, "has_api_key": bool(config.get("api_key"))}


@router.put("/config")
async def update_agents_config(
    config:  LLMConfig,
    request: Request,
) -> dict:
    """Persist user-supplied LLM config to Redis; graph falls back to rotator if unset."""
    redis_aio = request.app.state.redis_aio
    await redis_aio.json().set(
        CONFIG_REDIS_KEY,
        "$",
        config.model_dump(exclude_none = True),
    )
    return {
        "status": "saved",
        "config": config.model_dump(exclude = {"api_key"}),
    }


@router.post("/config/test")
async def test_agents_config(config: LLMConfig) -> dict:
    """Validate supplied credentials before saving: returns status/model/ms/reply."""
    return await ping_byok(config.model_dump(exclude_none = True))


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
    return {
        "status": "ok",
        "model":  "rotator (FGTS-VA across 7 providers)",
        "ms":     elapsed_ms,
        "reply":  str(reply)[:200],
    }


@router.get("/providers")
async def list_byok_providers() -> dict:
    """List enabled providers with a resolvable credential; NIM first then alphabetical."""
    items: list[dict] = []
    for pid, cfg in PROVIDERS.items():
        if not cfg.enabled:
            continue
        if not resolve_key(cfg.key_env):
            continue
        items.append({
            "id":       pid,
            "label":    _PROVIDER_LABELS.get(pid, pid.capitalize()),
            "key_env":  cfg.key_env,
        })
    items.sort(key = lambda x: (x["id"] != "nim", x["label"].lower()))
    return {"items": items, "total": len(items)}


@router.get("/providers/{provider_id}/models")
async def list_byok_provider_models(
    provider_id: str, request: Request,
) -> dict:
    """Live model list for a provider, ranked by rank_for_step("dd-all"); falls back to alphabetical on error."""
    cfg = PROVIDERS.get(provider_id)
    if cfg is None:
        raise HTTPException(
            status_code = 404,
            detail      = f"unknown provider {provider_id!r}",
        )
    if not resolve_key(cfg.key_env):
        return {
            "provider": provider_id,
            "items":    [],
            "total":    0,
            "error":    f"{cfg.key_env} not configured on Settings page",
        }
    try:
        by_provider = await list_all_alive_models(
            only_providers = [provider_id],
        )
    except Exception as e:
        raise HTTPException(
            status_code = 502,
            detail      = (
                f"discovery for {provider_id!r} failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            ),
        )
    records = [
        r for r in by_provider.get(provider_id, [])
        if r.model_id and not is_non_chat_model(r.model_id)
    ]
    if not records:
        return {"provider": provider_id, "items": [], "total": 0}
    redis_aio = getattr(request.app.state, "redis_aio", None)
    try:
        ranked = await rank_for_step("dd-all", records, redis = redis_aio)
        items = [r.model_id for r, _score in ranked]
    except Exception:
        items = sorted(r.model_id for r in records)
    return {"provider": provider_id, "items": items, "total": len(items)}


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
                    result = await graph.ainvoke(initial_state, config = config)
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
            outcome = "done",
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
    response = {
        "answer":             result.get("generation", "No answer generated."),
        "mode":               mode,
        "citations":          result.get("citations", []),
        "grounded":           result.get("grounded", False),
        "retrieval_sources":  result.get("retrieval_sources", []),
        "retry_count":        result.get("retry_count", 0),
        "search_query":       result.get("search_query", payload.question),
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
            mode = mode,
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
    preview_plan = bool(payload.preview_plan)

    turn_id: int | None = None
    if not preview_plan:
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
                latest = update["sub_results"][-1] if update["sub_results"] else None
                if isinstance(latest, dict):
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

    async def event_generator():
        from infra.langfuse.sessions import session as _lf_session
        _sess_id = payload.thread_id or DEFAULT_THREAD_ID
        _user_id = (effective_channel_ids or ["default"])[0]
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
            "preview_plan": preview_plan,
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
                        async for ev in graph.astream(
                            initial_state,
                            config      = config,
                            stream_mode = "updates",
                        ):
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
                            not preview_plan
                            and not saw_graph_event
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
                        if preview_plan and node_name == "plan_research":
                            set_current_span_langfuse_io(output_data = {
                                "status": "preview",
                                "mode": last_mode or payload.force_mode or "deep",
                                "sub_question_count": len(update.get("sub_questions") or []),
                                "research_plan": str(update.get("research_plan") or "")[:2000],
                            })
                            yield (
                                "data: "
                                + json.dumps({
                                    "node":   "end",
                                    "status": "preview",
                                })
                                + "\n\n"
                            )
                            return
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
                        })
                        + "\n\n"
                    )
                else:
                    if turn_id is not None and last_generation:
                        try:
                            thinking_state = _thinking_finalize(thinking_state)
                            _stamp_duration(thinking_state, t_run_start)
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


@router.post("/ingest/qdrant")
async def ingest_to_qdrant(payload: IngestRequest) -> dict:
    """Queue ES transcripts → Qdrant ingestion (Celery)."""
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
