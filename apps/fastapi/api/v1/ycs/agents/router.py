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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from domains.ycs.cache import cache_response, get_cached_response
from domains.ycs.conversation import get_history, save_turn
from domains.ycs.graph_builder import get_graph_stats

from .build import _serialize_update, build_graph_from_request
from .schemas import (
    GraphIngestRequest,
    IngestRequest,
    LLMConfig,
    PipelineRequest,
    RAGSearchRequest,
)


router = APIRouter()


# =============================================================================
# LLM Configuration
# =============================================================================
@router.put("/config")
async def update_agents_config(
    config:  LLMConfig,
    request: Request,
) -> dict:
    """Persist user-supplied LLM config to Redis JSON
    `coelhonexus:youtube:agents:config`. Strict port — does NOT route
    through the rotator's BYOK store (deprecated didn't).

    Source: deprecated `routers/v1/youtube/agents.py:L42-55`."""
    redis_aio = request.app.state.redis_aio
    await redis_aio.json().set(
        "coelhonexus:youtube:agents:config",
        "$",
        config.model_dump(exclude_none = True),
    )
    return {
        "status": "saved",
        "config": config.model_dump(exclude = {"api_key"}),
    }


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
    graph = build_graph_from_request(request)
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
    graph = build_graph_from_request(request)
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
        "recursion_limit": 100,
    }

    async def event_generator():
        last_generation = ""
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
                    serializable_update = _serialize_update(
                        node_name, update,
                    )
                    yield (
                        f"data: {json.dumps(serializable_update)}\n\n"
                    )
            if last_generation:
                await save_turn(
                    request.app.state.pg_url,
                    payload.thread_id,
                    payload.question,
                    last_generation,
                )
            yield (
                "data: "
                + json.dumps({"node": "end", "status": "complete"})
                + "\n\n"
            )
        except Exception as e:
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
