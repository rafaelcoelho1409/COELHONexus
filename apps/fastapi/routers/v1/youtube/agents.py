"""
YouTube Agentic RAG Router

Endpoints:
- PUT  /config         — Update LLM configuration
- POST /search         — Agentic RAG search (full invoke, returns final answer)
- POST /search/stream  — Agentic RAG search with SSE streaming (node-by-node updates)
- POST /ingest         — Ingest transcripts from ES into Qdrant (Phase 2)
- POST /ingest/graph   — Extract entities from chunks into Neo4j (Phase 3)
- GET  /graph/stats    — Knowledge graph node/relationship counts (Phase 3)
"""
import json
from fastapi import (
    APIRouter,
    HTTPException,
    Request
)
from fastapi.responses import StreamingResponse

from schemas.inputs import (
    LLMConfig,
    RAGSearchRequest,
    IngestRequest,
    GraphIngestRequest,
    PipelineRequest
)
from services.cache import get_cached_response, cache_response
from services.conversation import get_history, save_turn
from services.graph_builder import get_graph_stats
from .helpers import (
    _build_graph,
    _serialize_update
)


router = APIRouter()


# =============================================================================
# LLM Configuration
# =============================================================================
@router.put("/config")
async def update_agents_config(
    config: LLMConfig, 
    request: Request):
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
# Agentic RAG Search
# =============================================================================
@router.post("/search")
async def rag_search(
    payload: RAGSearchRequest, 
    request: Request):
    """
    Agentic RAG search: retrieves, grades, generates (or rewrites + retries).
    Returns the final answer with source documents and citations.

    Phase 4 additions:
    - Redis cache: identical questions return cached response instantly
    - Hallucination check: verifies answer is grounded in documents
    - FlashRank reranking: precision optimization after retrieval
    - Structured citations: video title + URL for each source
    """
    # Check cache first (skip cache for threaded conversations)
    if not payload.thread_id or payload.thread_id == "default":
        cached = await get_cached_response(
            request.app.state.redis_aio, 
            payload.question, 
            payload.force_mode)
        if cached:
            cached["_from_cache"] = True
            return cached
    # Load conversation history for this thread
    history = await get_history(
        request.app.state.pg_url, 
        payload.thread_id)
    graph = _build_graph(request)
    initial_state = {
        "question": payload.question,
        "mode": "",
        "force_mode": payload.force_mode or "",
        "conversation_history": history,
        "channel_ids": payload.channel_ids or [],
        "generation": "",
        "citations": [],
        "grounded": False,
        "retrieval_sources": [],
        "retry_count": 0,
        "search_query": payload.question,
        "sub_questions": [],
        "sub_results": [],
        "research_plan": "",
        "confidence_score": 0.0,
    }
    config = {
        "configurable": {
            "thread_id": payload.thread_id,
            "max_retries": payload.max_retries,
        },
        "recursion_limit": 100,  # DEEP mode needs headroom for parallel subagents
    }
    try:
        result = await graph.ainvoke(
            initial_state, 
            config = config)
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"Agent error: {str(e)}")
    mode = result.get("mode", "standard")
    response = {
        "answer": result.get("generation", "No answer generated."),
        "mode": mode,
        "citations": result.get("citations", []),
        "grounded": result.get("grounded", False),
        "retrieval_sources": result.get("retrieval_sources", []),
        "retry_count": result.get("retry_count", 0),
        "search_query": result.get("search_query", payload.question),
    }
    # Include deep-mode extras when applicable
    if mode == "deep":
        response["sub_questions"] = result.get("sub_questions", [])
        response["confidence_score"] = result.get("confidence_score", 0.0)
    # Save conversation turn to PostgreSQL
    await save_turn(
        request.app.state.pg_url, 
        payload.thread_id, 
        payload.question, 
        response["answer"], 
        mode)
    # Cache the response (only for non-threaded queries)
    if not payload.thread_id or payload.thread_id == "default":
        await cache_response(
            request.app.state.redis_aio, 
            payload.question, 
            response, 
            mode = mode)
    return response


@router.post("/search/stream")
async def rag_search_stream(
    payload: RAGSearchRequest, 
    request: Request):
    """
    Streaming Agentic RAG search via Server-Sent Events (SSE).

    CONCEPT: astream() yields updates as each node completes.
    The client receives real-time progress: which node is running, partial results, etc.
    """
    # Load conversation history for this thread
    history = await get_history(
        request.app.state.pg_url, 
        payload.thread_id)
    graph = _build_graph(request)
    initial_state = {
        "question": payload.question,
        "mode": "",
        "force_mode": payload.force_mode or "",
        "conversation_history": history,
        "channel_ids": payload.channel_ids or [],
        "generation": "",
        "citations": [],
        "grounded": False,
        "retrieval_sources": [],
        "retry_count": 0,
        "search_query": payload.question,
        "sub_questions": [],
        "sub_results": [],
        "research_plan": "",
        "confidence_score": 0.0,
    }
    config = {
        "configurable": {
            "thread_id": payload.thread_id,
            "max_retries": payload.max_retries,
        },
        "recursion_limit": 100,
    }
    async def event_generator():
        last_generation = ""
        try:
            async for event in graph.astream(
                initial_state, 
                config = config, 
                stream_mode = "updates"):
                for node_name, update in event.items():
                    if "generation" in update and update["generation"]:
                        last_generation = update["generation"]
                    serializable_update = _serialize_update(node_name, update)
                    yield f"data: {json.dumps(serializable_update)}\n\n"
            # Save conversation turn after streaming completes
            if last_generation:
                await save_turn(
                    request.app.state.pg_url, 
                    payload.thread_id, 
                    payload.question, 
                    last_generation)
            yield f"data: {json.dumps({'node': 'end', 'status': 'complete'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'node': 'error', 'error': str(e)})}\n\n"
    return StreamingResponse(
        event_generator(),
        media_type = "text/event-stream",
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/ingest")
async def ingest_to_qdrant(payload: IngestRequest):
    """
    Ingest transcripts from ES → Qdrant hybrid collection (Celery background task).
    Returns immediately with task_id.

    Flow: ES transcriptions → chunk → embed (dense + sparse) → Qdrant upsert
    """
    from tasks.ingestion import ingest_to_qdrant as ingest_task
    task = ingest_task.delay(
        payload.video_ids, 
        payload.chunk_size, 
        payload.chunk_overlap)
    return {
        "task_id": task.id, 
        "status": "queued", 
        "endpoint": f"/api/v1/tasks/{task.id}"}


@router.post("/ingest/graph")
async def ingest_to_graph(payload: GraphIngestRequest):
    """
    Extract entities from transcript chunks → Neo4j (Celery background task).
    Returns immediately with task_id.

    COST: Each chunk = 1 LLM call. 100 chunks ≈ 100 LLM calls.
    """
    from tasks.graph import ingest_to_graph as graph_task
    task = graph_task.delay(
        payload.video_ids, 
        payload.batch_size)
    return {
        "task_id": task.id, 
        "status": "queued", 
        "endpoint": f"/api/v1/tasks/{task.id}"}


@router.get("/graph/stats")
async def graph_stats(request: Request):
    """
    Get knowledge graph statistics from Neo4j.
    Returns node counts by label and relationship counts by type.
    """
    try:
        stats = await get_graph_stats(request.app.state.neo4j_graph)
        return stats
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"Graph stats error: {str(e)}")


@router.post("/pipeline")
async def full_pipeline(payload: PipelineRequest):
    """
    Full channel pipeline (Celery chain).
    Triggers: extract_channel → ingest_to_qdrant → ingest_to_graph → clear_cache

    Each step runs in its own Celery worker queue.
    Returns immediately with task_id.
    """
    from tasks.pipeline import full_channel_pipeline
    task = full_channel_pipeline.delay(
        payload.channel_id,
        payload.max_results,
        payload.include_transcription,
        payload.include_qdrant,
        payload.include_graph,
    )
    return {
        "task_id": task.id, 
        "status": "queued", 
        "endpoint": f"/api/v1/tasks/{task.id}"}
