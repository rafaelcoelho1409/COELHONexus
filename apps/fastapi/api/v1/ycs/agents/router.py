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

    async def event_generator():
        last_generation = ""
        last_mode       = ""
        last_persisted  = ""
        last_persist_t  = time.monotonic()
        try:
            async for event in graph.astream(
                initial_state,
                config      = config,
                stream_mode = "updates",
            ):
                for node_name, update in event.items():
                    if not isinstance(update, dict):
                        yield f"data: {json.dumps({'node': node_name})}\n\n"
                        continue
                    if "generation" in update and update["generation"]:
                        last_generation = update["generation"]
                    if "mode" in update and update["mode"]:
                        last_mode = update["mode"]
                    serializable_update = _serialize_update(
                        node_name, update,
                    )
                    yield (
                        f"data: {json.dumps(serializable_update)}\n\n"
                    )
                    # Throttled incremental persist — every N seconds
                    # while a stream is in flight. Refresh between
                    # writes shows the most recently committed
                    # snapshot, not nothing.
                    if (
                        turn_id is not None
                        and last_generation
                        and last_generation != last_persisted
                        and time.monotonic() - last_persist_t
                            >= _STREAM_PERSIST_INTERVAL_S
                    ):
                        try:
                            await update_turn_answer(
                                request.app.state.pg_url,
                                turn_id, last_generation, last_mode,
                            )
                            last_persisted = last_generation
                            last_persist_t = time.monotonic()
                        except Exception as e:
                            logger.warning(
                                f"[ycs:stream] incremental persist "
                                f"failed: {type(e).__name__}: {e}"
                            )
                    # P6 preview-plan halt: once `plan_research` emits
                    # the sub-questions, end the stream BEFORE the
                    # conditional edge fires the parallel sub-agent
                    # fan-out. The frontend will re-fire with the
                    # user's chosen (possibly pruned) sub_questions.
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
            # Final commit on successful completion.
            if turn_id is not None and last_generation:
                try:
                    await update_turn_answer(
                        request.app.state.pg_url,
                        turn_id, last_generation, last_mode,
                    )
                except Exception as e:
                    logger.warning(
                        f"[ycs:stream] final persist failed: "
                        f"{type(e).__name__}: {e}"
                    )
            elif turn_id is not None and not last_generation:
                # Graph finished without ever producing a generation —
                # nothing to save; drop the placeholder so the picker
                # stays clean.
                try:
                    await delete_turn(request.app.state.pg_url, turn_id)
                except Exception:
                    pass
            yield (
                "data: "
                + json.dumps({"node": "end", "status": "complete"})
                + "\n\n"
            )
        except Exception as e:
            # On error: persist whatever generation arrived (if any)
            # rather than losing it — the user can refresh and at
            # least see the partial answer they were waiting for.
            if turn_id is not None:
                try:
                    if last_generation:
                        await update_turn_answer(
                            request.app.state.pg_url,
                            turn_id, last_generation, last_mode,
                        )
                    else:
                        await delete_turn(
                            request.app.state.pg_url, turn_id,
                        )
                except Exception:
                    pass
            yield (
                "data: "
                + json.dumps({"node": "error", "error": str(e)})
                + "\n\n"
            )

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
