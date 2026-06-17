"""ycs/agents — agentic RAG router (deprecated youtube/agents.py).

Direct port of deprecated `routers/v1/youtube/agents.py:L42-298`.

7 endpoints:
  PUT  /config         — update LLM configuration in Redis JSON
  POST /search         — agentic RAG (full invoke → final answer)
  POST /search/stream  — agentic RAG with SSE node-by-node updates
  POST /ingest/qdrant  — queue ES transcripts → Qdrant (Celery)
  POST /ingest/neo4j   — queue entity extraction → Neo4j (Celery)
  GET  /graph/stats    — Neo4j node/relationship counts
  POST /pipeline       — Celery chain (extract → Qdrant → Neo4j → cache)"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from domains.ycs.cache import cache_response, get_cached_response
from domains.ycs.conversation import (
    DEFAULT_THREAD_ID,
    branch_thread,
    delete_thread,
    delete_turn,
    get_history,
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


# Human-readable provider labels for the BYOK dropdown. Stays here
# rather than in the discovery config so the UI's wording is decoupled
# from the rotator's internal identifiers.
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

# Min seconds between two incremental Postgres writes while a stream is
# in flight. Trade-off: smaller = refresh-mid-stream shows closer-to-
# real-time partial answers; larger = fewer round-trips to PG when the
# LLM is bursting tokens.
_STREAM_PERSIST_INTERVAL_S = 2.5

# 2026-06-15 — watchdog ceiling on the time between two consecutive
# `graph.astream()` events. Several adaptive-graph nodes
# (`check_hallucination`, `critic`, `contextualize`, `rewrite_query`,
# plus the per-call grader path) currently lack inner timeouts. If one
# silently hangs (an LLM that neither 429s nor errors but just stops
# responding — seen in the 2026-06-15 free-tier rate-pressure storm),
# the whole `event_generator` await blocks forever and the cancellation
# checks never run. 15 minutes is generous: a wave-1 DEEP sub-agent
# (cap=3, max_retries=1, recursion_limit=12) takes ~5–13 min worst
# case, so 15 min is ~3× margin over the slowest legitimate event
# gap. When the watchdog trips we persist a sentinel answer + emit
# `end status=stalled` so the user sees a real failure instead of a
# spinner that never advances.
_LANGGRAPH_WATCHDOG_S = 15 * 60.0

# 2026-06-15 — hard ceiling on every Postgres write the SSE loop
# performs (incremental persists + heartbeats + finalize). Without this
# a hung PG connection wedges the WHOLE consumer task: the heartbeat
# stops bumping `_seq`, the frontend sees a flatlined snapshot, and
# the user gets the false-positive "Stalled" label even though the
# producer task is still happily streaming graph events. 3 s is
# generous for a single-row UPDATE on a healthy database; any slower
# than that is a real backend issue and we'd rather skip the persist
# than block the entire generator.
_PERSIST_TIMEOUT_S = 3.0

# 2026-06-15 — turn IDs currently marked for cancellation by the
# `POST /turns/{turn_id}/cancel` endpoint. The SSE `event_generator`
# checks this set between events and breaks early when its turn_id
# lands in it, freeing the LangGraph pipeline + LLM calls instead of
# letting the orphaned graph run to completion. In-memory only (one
# FastAPI worker assumption — single-pod dev setup); upgrade to
# Redis if we ever scale to >1 worker. Always cleaned up in the
# generator's `finally` so it can't grow unboundedly.
_CANCELLED_TURN_IDS: set[int] = set()


# =============================================================================
# LLM Configuration
# =============================================================================
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
    """Persist user-supplied LLM config to Redis JSON
    `coelhonexus:youtube:agents:config`. The Adaptive RAG graph reads
    this back per request via `build.build_graph_from_request()` →
    `byok.get_byok_config()`. If unset or missing `api_key`, the graph
    falls back to `app.state.llm` (the rotator chain).

    Source: deprecated `routers/v1/youtube/agents.py:L42-55`."""
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
    """Fire one `ainvoke("ping")` round-trip against the supplied config
    so the user can validate credentials BEFORE saving. Returns
    `{"status": "ok", "model": "...", "ms": int, "reply": "..."}` on
    success, `{"status": "error", "error": "..."}` otherwise."""
    return await ping_byok(config.model_dump(exclude_none = True))


@router.post("/rotator/ping")
async def rotator_ping(request: Request) -> dict:
    """Connectivity check against the LIVE rotator chain (`app.state.llm`).
    Same shape as `ping_byok` so the frontend's Test button can call
    either endpoint interchangeably. Used by the Ask page's LLM info
    card to verify the rotator is responsive."""
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


# =============================================================================
# Provider + model dropdowns
# =============================================================================
@router.get("/providers")
async def list_byok_providers() -> dict:
    """List the providers the Ask page can actually use right now —
    only those that are `enabled=True` in the rotator registry AND have
    a key resolvable from the credential store (Settings page). The
    user manages keys on `/settings`, not here.

    Sort: NVIDIA NIM first (the canonical free-tier per the rotator
    memory), then alphabetical."""
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
    """Live model list for one provider, fetched from its /v1/models via
    the rotator's discovery layer + ranked best-first by the same
    `rank_for_step("dd-all", ...)` the dynamic catalog uses.

    Ranking pipeline (same path the catalog builder takes):
      1. Discovery fan-out → `DiscoveryRecord`s for this provider.
      2. Drop non-chat models (embedders / rerankers / ASR / TTS).
      3. `rank_for_step("dd-all", records, redis=app.state.redis_aio)`
         — composite leaderboard score + provider-tier tiebreak.
      4. Materialize ordered model_ids: highest score first.

    If ranking fails (transient leaderboard fetch error), fall back to
    the unranked discovery list so the picker still works — alphabetical
    is better than empty."""
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


# =============================================================================
# Conversation history (for UI rehydration on page refresh)
# =============================================================================
@router.get("/history/{thread_id}")
async def get_thread_history(thread_id: str, request: Request) -> dict:
    """Return the full Q+A history for `thread_id` so the UI can re-render
    prior turns when the user refreshes the Ask page. Returns an empty
    list for the `default` sentinel or any unknown thread."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return {"thread_id": thread_id, "items": [], "total": 0}
    items = await list_thread_messages(
        request.app.state.pg_url, thread_id,
    )
    return {"thread_id": thread_id, "items": items, "total": len(items)}


@router.get("/threads")
async def get_threads(request: Request) -> dict:
    """List existing conversation threads (most-recent first) so the UI
    can render a picker. Each item carries `{thread_id, turn_count,
    last_seen, first_question}` — enough for the picker to show a
    sensible per-thread row."""
    items = await list_threads(request.app.state.pg_url)
    return {"items": items, "total": len(items)}


@router.post("/threads/{thread_id}/branch")
async def branch_thread_endpoint(
    thread_id: str,
    request:   Request,
) -> dict:
    """Branch a conversation: create a new thread that mirrors the
    source up to a specific point, then carry on independently.

    Request body (optional): `{"up_to_created_at": "<iso-8601>"}`.
    `None` or missing → copy the whole source. The new thread id is
    generated server-side so the frontend can switch to it directly.

    Returns `{"new_thread_id": str, "copied": N}`."""
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
    """Delete a conversation thread and all of its turns from Postgres.

    Returns `{"deleted": N}` where N is the row count actually removed
    (0 if the thread did not exist — caller treats both states the
    same way and refreshes the picker). The `default` sentinel is
    no-op'd."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return {"deleted": 0}
    n = await delete_thread(request.app.state.pg_url, thread_id)
    return {"deleted": n}


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn_endpoint(
    turn_id: int,
    request: Request,
) -> dict:
    """Cancel an in-flight turn (frontend Stop button).

    Two things happen here, in this order:
      1. The turn_id is added to `_CANCELLED_TURN_IDS` so the matching
         SSE `event_generator` (which checks this set between LangGraph
         updates) breaks out of its loop ASAP and frees the rotator +
         LLM resources.
      2. The Postgres placeholder row is deleted so the conversation
         picker stops showing the cancelled question.

    Idempotent — a second click (or a stale browser tab) just returns
    `{"deleted": 0}`. Always returns 200 even if the SSE was already
    finished by the time this lands."""
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


# =============================================================================
# Agentic RAG search (full-invoke)
# =============================================================================
@router.post("/search")
async def rag_search(
    payload: RAGSearchRequest,
    request: Request,
) -> dict:
    """Agentic RAG: classify → (retrieve → grade → generate → ...) → answer.

    Deprecated routing (agents.py:L62-149):
      1. Check Redis cache (skip for threaded conversations)
      2. Load thread history from Postgres
      3. Build adaptive graph from app.state
      4. `await graph.ainvoke(initial_state, config)`
      5. Save turn to Postgres
      6. Cache response (non-threaded only)"""
    # Cache check (skip for threaded conversations)
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
        # DEEP mode needs headroom for parallel subagents.
        "recursion_limit": 100,
    }
    try:
        result = await graph.ainvoke(initial_state, config = config)
    except Exception as e:
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


# =============================================================================
# Agentic RAG search (SSE stream)
# =============================================================================
@router.post("/search/stream")
async def rag_search_stream(
    payload: RAGSearchRequest,
    request: Request,
) -> StreamingResponse:
    """Streaming agentic RAG via Server-Sent Events.

    `astream(stream_mode='updates')` yields one event per node completion.
    The client sees real-time progress (which node is running, partial
    documents, generations). After the loop, saves the final answer to
    Postgres + emits a terminator event.

    Deprecated source: agents.py:L153-223."""
    history = await get_history(
        request.app.state.pg_url, payload.thread_id,
    )
    graph = await build_graph_from_request(request)
    initial_state = {
        "question":             payload.question,
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
        # P6 (2026-06-14): caller may pre-supply sub_questions to skip
        # the planner LLM call (frontend's second pass after user
        # confirms / prunes the previewed plan).
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

    # 2026-06-14 — insert the turn placeholder NOW so the conversation
    # survives mid-stream refresh / network drop / hang. The row's
    # `answer` starts empty and gets UPDATE'd both incrementally as
    # generation streams in AND on a successful end. If the stream
    # errors out before ANY generation lands, the placeholder is
    # deleted (no haunted empty-answer row in the picker). Preview-mode
    # turns are not persisted at all (no commit yet).
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

    # 2026-06-15 — `thinking_state` accumulator. Mirrors the frontend's
    # NODE_ACTION_TEXT map + STAGE_ORDER so a refresh anywhere during
    # the stream restores the Thinking expander to its exact state.
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
            # Defensive: `setdefault` won't replace an EXISTING `None`
            # value, only an absent key. Initialise explicitly when
            # `deep` is missing OR `None` (legacy / partial state).
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
        # `run_subagent` returns `{"sub_results": [{"sub_question": ...,
        # "answer": ..., ...}]}` via the `operator.add` reducer. The
        # SSE-facing `_serialize_update` flattens that to
        # `latest_sub_question` / `latest_sub_answer_preview` for the
        # frontend's convenience — but THIS code path runs on the RAW
        # graph update, where only `sub_results` is present. Read from
        # the last appended sub-result so each completion flips the
        # matching card from queued → done with the preview attached.
        if update.get("sub_results"):
            deep = state.get("deep")
            if isinstance(deep, dict) and isinstance(deep.get("sub_questions"), list):
                latest = update["sub_results"][-1] if update["sub_results"] else None
                if isinstance(latest, dict):
                    target = latest.get("sub_question", "") or ""
                    # 2026-06-15 — store the FULL sub-answer (was a
                    # 200-char preview). The UI renders done sub-question
                    # cards as collapsed expanders with the full
                    # markdown answer inside, so the preview slice
                    # truncated the bulk of each sub-agent's output.
                    full_answer = latest.get("answer", "") or ""
                    # 2026-06-16 — sub-agents now emit `error_kind` so
                    # the UI placeholder can be specific. The actual
                    # placeholder text is composed inside `run_subagent`
                    # (`subagent/node.py::_classify_subagent_outcome`)
                    # so the failure-mode prose lives next to the code
                    # that detects it. This branch just propagates it
                    # forward + sets a generic fallback for any
                    # belt-and-suspenders empty-answer edge case the
                    # subagent didn't cover.
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
                            # Keep `answer_preview` for back-compat with
                            # any old persisted state from before this
                            # commit so refreshes don't see "" mid-flight.
                            sq["answer_preview"] = full_answer[:200]
                            break
                # 2026-06-15 — advance the visible retrieve label so the
                # UI doesn't sit on "Classifying intent" / "Planning sub-
                # questions" for the entire ~30 min DEEP fan-out. The
                # LangGraph `stream_mode="updates"` only emits on node
                # completion, so without this nudge the user has no
                # signal that work is progressing besides the sub-
                # question cards (which flip in waves, not continuously).
                # We piggy-back on every `sub_results` event to refresh a
                # "Researching sub-questions (N/M done)" subtitle.
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
        """Stamp `state["duration_ms"]` with the wall-clock elapsed since
        `t_start` (monotonic anchor) and return the value. Called at every
        terminal persist path so the frontend's "Answered in X.Xs" badge
        survives both the SSE _done frame AND the history-reload path
        without needing a separate column.

        Idempotent — overwriting on cancelled/error → success retry is the
        right behavior (final stamp wins)."""
        ms = int((time.monotonic() - t_start) * 1000)
        state["duration_ms"] = ms
        return ms

    def _stamp_citations(state: dict, citations: list) -> None:
        """Fold the latest citations into `state["citations"]` so they
        ride the JSONB column to Postgres. Called from every terminal
        persist path alongside `_stamp_duration` and `_thinking_finalize`.

        The frontend's `renderHistoryTurn` reads this back to rehydrate
        the right-rail Sources panel + restore inline `[N]` pills on past
        turns. Without this, citations were lost the moment the SSE
        connection closed (they were only held in the JS-side
        `turnDataMap` WeakMap, GC'd on page navigation)."""
        if isinstance(citations, list) and citations:
            state["citations"] = citations

    async def event_generator():
        last_generation = ""
        last_mode       = ""
        last_persisted  = ""
        # 2026-06-16 — track citations server-side so they get folded
        # into the persisted `thinking_state` and survive page reload.
        # `conversation_history` only stores answer/mode/thinking_state
        # /created_at — no dedicated citations column — so we ride the
        # JSONB. Frontend reads `thinking_state.citations` in
        # `renderHistoryTurn` to rehydrate the right-rail + restore
        # inline `[N]` pills on past turns.
        last_citations: list = []
        # 2026-06-16 — `t_run_start` is the wall-clock anchor for
        # the per-turn "Answered in X.Xs" UI badge. Captured at SSE
        # generator entry (the moment the user's Send fires) and
        # stamped into `thinking_state["duration_ms"]` at every
        # terminal persist path (success, watchdog/stalled,
        # cancelled, error). Reused as the SSE `_done` frame's
        # `duration_ms` field so the live tab can show the badge
        # without waiting for the next history load. Distinct from
        # `last_persist_t` (which is just the streaming-incremental
        # PG-throttle clock).
        t_run_start     = time.monotonic()
        last_persist_t  = t_run_start
        first_persist_done = False
        thinking_state: dict = {"stages": {}, "mode": ""}
        cancelled = False
        stalled   = False
        # 2026-06-15 — backend "I'm alive" heartbeat folded into the
        # main loop. The frontend's stalled-detection (6 identical poll
        # snapshots in a row → ~15 s flatline) was firing false
        # positives during DEEP-mode sub-agent fan-out: `_subagent`
        # invokes the STANDARD sub-graph via `ainvoke`, which yields
        # ZERO events to the parent's `astream` until the sub-agent
        # fully completes. With one sub-agent legitimately running for
        # 5–15 min, the parent's `thinking_state` stayed byte-identical
        # for the whole window and the frontend marked it stalled.
        #
        # Earlier this was a separate `asyncio.Task`; that task died
        # silently (likely a CancelledError propagating from somewhere
        # in the LangGraph internals that wasn't caught by
        # `except Exception` — `CancelledError` derives from
        # `BaseException` in Py 3.8+). Folding the heartbeat into the
        # main loop via `asyncio.wait_for(__anext__(), timeout=2.5)`
        # means every tick is either a graph event (reset the watchdog
        # counter, process normally) or a `TimeoutError` (bump `_seq`,
        # persist, advance the watchdog counter). No background task to
        # race or die.
        hb_seq                  = 0
        heartbeats_since_event  = 0
        _MAX_HEARTBEATS_BEFORE_WATCHDOG = int(
            _LANGGRAPH_WATCHDOG_S / _STREAM_PERSIST_INTERVAL_S
        )
        try:
            # 2026-06-15 — emit the turn_id to the client as the very
            # first SSE frame. The frontend stashes it on
            # `currentTurnEl.dataset.turnId` so the Stop button can fire
            # `POST /turns/{turn_id}/cancel` even after a page refresh
            # (where the original `AbortController` is gone with the
            # previous JS context). `_meta` is namespaced so the regular
            # `applyUpdate` switch on `node` never confuses it for a
            # graph node update.
            if turn_id is not None:
                yield (
                    "data: "
                    + json.dumps({"node": "_meta", "turn_id": turn_id})
                    + "\n\n"
                )
            # 2026-06-15 — producer/consumer over `graph.astream(...)`.
            # An earlier design wrapped each `__anext__()` directly in
            # `asyncio.wait_for(timeout=2.5)` to combine the heartbeat
            # tick and the graph-event wait into a single await. That
            # was a SUBTLE BUG: `wait_for` cancels the inner coroutine
            # on timeout, which closes the underlying async generator.
            # The next `__anext__` then returned `StopAsyncIteration`
            # immediately and the SSE generator exited via the natural
            # "no-generation finalize" branch — turns landed in PG with
            # the sentinel answer + all four stages marked done but
            # 0 sub-questions actually completed. The producer task
            # below drives the LangGraph stream WITHOUT being cancelled
            # by timeouts; we only cancel it in `finally`, after the
            # consumer has decided to bail. Backpressure is provided by
            # `maxsize=1` on the queue (LangGraph never gets more than
            # one event ahead of the consumer).
            event_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
            async def _producer():
                try:
                    async for ev in graph.astream(
                        initial_state,
                        config      = config,
                        stream_mode = "updates",
                    ):
                        await event_queue.put(("event", ev))
                    await event_queue.put(("done", None))
                except asyncio.CancelledError:
                    # Consumer cancelled us in `finally` — silent
                    # shutdown. The graph's own cleanup (HTTP
                    # connection closing, etc.) propagates upward via
                    # the `async for`'s teardown.
                    raise
                except Exception as e:
                    # Any LangGraph-side exception surfaces here.
                    # Forward it to the consumer so it can be
                    # propagated into the outer `except` and trigger
                    # the normal error-finalize / error SSE frame.
                    try:
                        await event_queue.put(("error", e))
                    except Exception:
                        pass
            producer_task = asyncio.create_task(_producer())
            try:
                while True:
                    # ── Cancellation check ───────────────────────────
                    # Two signals collapse here: the explicit
                    # `/turns/{turn_id}/cancel` set (set by the Stop
                    # button, works across page refreshes) AND the
                    # native client disconnect detected by Starlette
                    # (browser tab closed / page refreshed / Stop
                    # clicked so the fetch aborts).
                    if turn_id is not None and turn_id in _CANCELLED_TURN_IDS:
                        cancelled = True
                        break
                    if await request.is_disconnected():
                        cancelled = True
                        break
                    try:
                        kind, payload = await asyncio.wait_for(
                            event_queue.get(),
                            timeout = _STREAM_PERSIST_INTERVAL_S,
                        )
                    except asyncio.TimeoutError:
                        # ── Heartbeat tick ───────────────────────────
                        # 2.5 s passed with no graph event. Bump
                        # `_seq` on the persisted snapshot so the
                        # frontend's stalled detection sees forward
                        # motion, then check if we blew the long-
                        # running-node watchdog.
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
                        # 2026-06-16 — KEEP-ALIVE SSE frame. The
                        # browser's `fetch()` reader drops the
                        # connection after ~60–120 s of TCP silence
                        # (network stacks, proxies, browser idle
                        # timeouts). Long-running DEEP runs sit silent
                        # for 5–10+ min while a sub-agent grinds
                        # through its sub-graph — the parent `astream`
                        # yields ZERO events during that window. The
                        # backend heartbeat above writes to Postgres
                        # but nothing flows over the SSE wire, so the
                        # client got disconnected and we lost the
                        # whole run.
                        #
                        # SSE comment frames (`: text\n\n`) keep the
                        # connection alive at the TCP layer — the
                        # client's parser explicitly ignores them, but
                        # they count as activity for every timeout
                        # mechanism in the stack.
                        yield f": heartbeat {hb_seq}\n\n"
                        continue
                    if kind == "done":
                        break
                    if kind == "error":
                        raise payload  # propagate to outer except
                    # kind == "event"
                    event = payload
                    # Got a real graph event — reset the watchdog counter.
                    heartbeats_since_event = 0
                    for node_name, update in event.items():
                        if not isinstance(update, dict):
                            yield f"data: {json.dumps({'node': node_name})}\n\n"
                            continue
                        if "generation" in update and update["generation"]:
                            last_generation = update["generation"]
                        if "mode" in update and update["mode"]:
                            last_mode = update["mode"]
                        # 2026-06-16 — capture citations parallel to
                        # `last_generation` so the terminal persists can
                        # fold them into `thinking_state["citations"]`.
                        # Both STANDARD's `format_citations`, the new
                        # `fallback_answer`, and DEEP's `synthesize`
                        # emit `citations` in their update payload.
                        if isinstance(update.get("citations"), list):
                            last_citations = update["citations"]
                        # Accumulate the Thinking expander state so a
                        # refresh mid-stream restores it exactly.
                        thinking_state = _thinking_apply(
                            thinking_state, node_name, update,
                        )
                        serializable_update = _serialize_update(
                            node_name, update,
                        )
                        yield (
                            f"data: {json.dumps(serializable_update)}\n\n"
                        )
                        # 2026-06-15 — persist thinking_state
                        # INDEPENDENTLY of generation. Earlier version
                        # gated this on `last_generation`, but
                        # generation only lands at the 4th stage (or
                        # never if the rotator fails) — so a refresh
                        # during Retrieve / Grade would show null
                        # state. Fire ASAP on the first event so the
                        # placeholder gets its stage status quickly,
                        # then throttle subsequent persists at
                        # `_STREAM_PERSIST_INTERVAL_S`. The first-fire
                        # guarantees that even a Retrieve-only stream
                        # restores its progress on refresh.
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
                        # P6 preview-plan halt: once `plan_research`
                        # emits the sub-questions, end the stream
                        # BEFORE the conditional edge fires the
                        # parallel sub-agent fan-out. The frontend
                        # will re-fire with the user's chosen
                        # (possibly pruned) sub_questions.
                        if preview_plan and node_name == "plan_research":
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
                    # Cancellation path: the `/turns/{turn_id}/cancel`
                    # endpoint already DELETE'd the placeholder row,
                    # so any final persist here would either
                    # UPDATE-no-row (best case) or recreate stale state
                    # (worst case). Skip both the success-finalize and
                    # the no-generation-sentinel blocks; just emit a
                    # terminal frame for any consumer that hasn't
                    # dropped yet.
                    logger.info(
                        f"[ycs:stream] cancelled mid-flight turn_id={turn_id}"
                    )
                    yield (
                        "data: "
                        + json.dumps({"node": "end", "status": "cancelled"})
                        + "\n\n"
                    )
                elif stalled:
                    # Watchdog tripped — some node hung silently and
                    # never yielded an event for 15 minutes. Persist a
                    # clear sentinel so the user sees a real failure
                    # on refresh instead of an "in progress" spinner.
                    if turn_id is not None:
                        try:
                            thinking_state = _thinking_finalize(thinking_state)
                            _stamp_duration(thinking_state, t_run_start)
                            _stamp_citations(thinking_state, last_citations)
                            sentinel = (
                                "(no response — pipeline stalled after "
                                f"{int(_LANGGRAPH_WATCHDOG_S / 60)} min "
                                "of silence. A node hung without a "
                                "timeout; expand Thinking to see the "
                                "last reachable step.)"
                            )
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
                    # Final commit on successful completion.
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
                        # 2026-06-15 — graph finished without ever
                        # producing a generation (rotator exhausted,
                        # retries blown, etc.). Preserve the
                        # placeholder with the FINAL thinking_state +
                        # a clear "no response" sentinel in `answer`.
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
                # Cancel the producer task on EVERY exit path —
                # natural completion, cancellation, watchdog, exception.
                # Without this, an aborted SSE could leave the LangGraph
                # stream running in the background until it timed out
                # naturally, wasting LLM credits.
                producer_task.cancel()
                try:
                    await producer_task
                except BaseException:
                    pass
        except asyncio.CancelledError:
            # 2026-06-16 — DEDICATED cancellation handler. Earlier this
            # path fell through to the unhandled-BaseException case,
            # which meant: the SSE generator silently terminated, the
            # placeholder row in PG stayed frozen at its last heartbeat
            # snapshot, the `_seq` counter stopped advancing, and the
            # frontend's stalled-detection (correctly) flagged the
            # backend as dead — but the user had no way to tell whether
            # the run was truly stalled or just disconnected. We now
            # write a clear "interrupted" sentinel + finalize the
            # `thinking_state` stages so a refresh renders a real
            # failure UI instead of an "in progress" spinner.
            #
            # Triggered by: Starlette closing the response when the
            # browser disconnects (refresh, tab close, network drop)
            # OR an outer asyncio task cancellation. Either way the
            # client is gone — there's no point yielding more SSE
            # frames; just persist the sentinel and re-raise so
            # asyncio teardown propagates upward cleanly.
            #
            # CRITICAL: an `await` INSIDE this handler re-raises
            # CancelledError immediately (asyncio cancellation
            # semantics), which silently aborted the persist on the
            # first attempt at this fix. To actually complete the
            # write we spawn it as a DETACHED `asyncio.create_task()`
            # — the event loop keeps the task alive after the
            # generator returns, so the PG UPDATE runs to completion
            # in the background regardless of our own cancellation.
            logger.info(
                f"[ycs:stream] cancelled (client disconnect) "
                f"turn_id={turn_id} — scheduling sentinel persist and re-raising"
            )
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
                        # We're in a detached task — log so we can
                        # diagnose if the cancellation sentinel ever
                        # fails to land.
                        logger.warning(
                            f"[ycs:stream] cancellation sentinel "
                            f"persist failed for turn_id={tid}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                # Detached task — survives our own cancellation
                # propagation. asyncio keeps a strong reference via
                # the loop's pending-tasks set, so it won't be GC'd
                # before it runs.
                asyncio.create_task(
                    _persist_cancellation_sentinel(
                        request.app.state.pg_url,
                        turn_id, answer_text, last_mode,
                        thinking_state_snapshot,
                    )
                )
            raise
        except Exception as e:
            # On error: persist whatever generation arrived (if any)
            # rather than losing it — the user can refresh and at
            # least see the partial answer they were waiting for.
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
            # 2026-06-15 — always drop this turn from the cancel set
            # so it can't grow unboundedly. `discard` is the no-raise
            # variant — handles both the cancelled and the natural-
            # completion paths uniformly. (No background heartbeat
            # task to cancel — the heartbeat is folded into the main
            # event loop above via `asyncio.wait_for(timeout=2.5)`.)
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


# =============================================================================
# Ingestion dispatch
# =============================================================================
@router.post("/ingest/qdrant")
async def ingest_to_qdrant(payload: IngestRequest) -> dict:
    """Ingest transcripts from ES → Qdrant hybrid collection (Celery).

    Source: deprecated agents.py:L226-242."""
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
    """Extract entities from transcripts → Neo4j (Celery).

    Each transcript = 1 LLM call. 100 transcripts ≈ 100 LLM calls.

    Source: deprecated agents.py:L245-260."""
    from domains.ycs.neo4j_task.task import ingest_to_neo4j as graph_task
    task = graph_task.delay(payload.video_ids, payload.batch_size)
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


# =============================================================================
# Knowledge graph stats
# =============================================================================
@router.get("/graph/stats")
async def graph_stats(request: Request) -> dict:
    """Get Neo4j node counts by label + relationship counts by type.

    Source: deprecated agents.py:L263-275."""
    try:
        stats = await get_graph_stats(request.app.state.neo4j_graph)
        return stats
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail      = f"Graph stats error: {str(e)}",
        )


# =============================================================================
# Full pipeline (Celery chain)
# =============================================================================
@router.post("/pipeline")
async def full_pipeline(payload: PipelineRequest) -> dict:
    """Full channel pipeline (Celery chain).

    Triggers: extract_channel → ingest_to_qdrant → ingest_to_neo4j → cache.
    Each step runs in its own Celery worker queue.

    Source: deprecated agents.py:L278-298."""
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
