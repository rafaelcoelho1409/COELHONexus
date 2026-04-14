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
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from schemas.inputs import LLMConfig, RAGSearchRequest
from services.retriever import (
    ElasticsearchRetriever,
    QdrantHybridRetriever,
    Neo4jRetriever,
    SmartRetriever,
)
from services.grader import DocumentGrader
from agents.adaptive import build_adaptive_rag_graph


router = APIRouter()


# =============================================================================
# LLM Configuration
# =============================================================================
@router.put("/config")
async def update_agents_config(config: LLMConfig, request: Request):
    redis_aio = request.app.state.redis_aio
    await redis_aio.json().set(
        "coelhonexus:youtube:agents:config",
        "$",
        config.model_dump(exclude_none=True),
    )
    return {
        "status": "saved",
        "config": config.model_dump(exclude={"api_key"}),
    }


# =============================================================================
# Agentic RAG Search
# =============================================================================
@router.post("/search")
async def rag_search(body: RAGSearchRequest, request: Request):
    """
    Agentic RAG search: retrieves, grades, generates (or rewrites + retries).
    Returns the final answer with source documents and citations.

    Phase 4 additions:
    - Redis cache: identical questions return cached response instantly
    - Hallucination check: verifies answer is grounded in documents
    - FlashRank reranking: precision optimization after retrieval
    - Structured citations: video title + URL for each source
    """
    from services.cache import get_cached_response, cache_response

    # Check cache first
    cached = await get_cached_response(request.app.state.redis_aio, body.question, body.force_mode)
    if cached:
        cached["_from_cache"] = True
        return cached

    graph = _build_graph(request)
    initial_state = {
        "question": body.question,
        "mode": "",
        "force_mode": body.force_mode or "",
        "channel_ids": body.channel_ids or [],
        "generation": "",
        "citations": [],
        "grounded": False,
        "retrieval_sources": [],
        "retry_count": 0,
        "search_query": body.question,
        "sub_questions": [],
        "sub_results": [],
        "research_plan": "",
        "confidence_score": 0.0,
    }
    config = {
        "configurable": {
            "thread_id": body.thread_id,
            "max_retries": body.max_retries,
        },
        "recursion_limit": 100,  # DEEP mode needs headroom for parallel subagents
    }
    try:
        result = await graph.ainvoke(initial_state, config = config)
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
        "search_query": result.get("search_query", body.question),
    }
    # Include deep-mode extras when applicable
    if mode == "deep":
        response["sub_questions"] = result.get("sub_questions", [])
        response["confidence_score"] = result.get("confidence_score", 0.0)

    # Cache the response
    await cache_response(request.app.state.redis_aio, body.question, response, mode=mode)

    return response


@router.post("/search/stream")
async def rag_search_stream(body: RAGSearchRequest, request: Request):
    """
    Streaming Agentic RAG search via Server-Sent Events (SSE).

    CONCEPT: astream() yields updates as each node completes.
    The client receives real-time progress: which node is running, partial results, etc.
    """
    graph = _build_graph(request)
    initial_state = {
        "question": body.question,
        "mode": "",
        "force_mode": body.force_mode or "",
        "channel_ids": body.channel_ids or [],
        "generation": "",
        "citations": [],
        "grounded": False,
        "retrieval_sources": [],
        "retry_count": 0,
        "search_query": body.question,
        "sub_questions": [],
        "sub_results": [],
        "research_plan": "",
        "confidence_score": 0.0,
    }
    config = {
        "configurable": {
            "thread_id": body.thread_id,
            "max_retries": body.max_retries,
        },
        "recursion_limit": 100,
    }

    async def event_generator():
        try:
            async for event in graph.astream(initial_state, config = config, stream_mode = "updates"):
                for node_name, update in event.items():
                    serializable_update = _serialize_update(node_name, update)
                    yield f"data: {json.dumps(serializable_update)}\n\n"
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


# =============================================================================
# Ingestion (Phase 2)
# =============================================================================
class IngestRequest(BaseModel):
    """
    Request to ingest transcripts from ES into Qdrant.
    If video_ids is None, ingests ALL transcripts in ES.
    """
    video_ids: list[str] | None = None
    chunk_size: int = 2000
    chunk_overlap: int = 200


@router.post("/ingest")
async def ingest_to_qdrant(body: IngestRequest):
    """
    Ingest transcripts from ES → Qdrant hybrid collection (Celery background task).
    Returns immediately with task_id.

    Flow: ES transcriptions → chunk → embed (dense + sparse) → Qdrant upsert
    """
    from tasks.ingestion import ingest_to_qdrant as ingest_task
    task = ingest_task.delay(body.video_ids, body.chunk_size, body.chunk_overlap)
    return {"task_id": task.id, "status": "queued", "endpoint": f"/api/v1/tasks/{task.id}"}


# =============================================================================
# Knowledge Graph (Phase 3)
# =============================================================================
class GraphIngestRequest(BaseModel):
    """
    Request to extract entities from full transcripts into Neo4j.
    If video_ids is None, processes ALL transcripts in ES.
    batch_size controls concurrent LLM calls per batch.
    """
    video_ids: list[str] | None = None
    batch_size: int = 3


@router.post("/ingest/graph")
async def ingest_to_graph(body: GraphIngestRequest):
    """
    Extract entities from transcript chunks → Neo4j (Celery background task).
    Returns immediately with task_id.

    COST: Each chunk = 1 LLM call. 100 chunks ≈ 100 LLM calls.
    """
    from tasks.graph import ingest_to_graph as graph_task
    task = graph_task.delay(body.video_ids, body.batch_size)
    return {"task_id": task.id, "status": "queued", "endpoint": f"/api/v1/tasks/{task.id}"}


@router.get("/graph/stats")
async def graph_stats(request: Request):
    """
    Get knowledge graph statistics from Neo4j.
    Returns node counts by label and relationship counts by type.
    """
    from services.graph_builder import get_graph_stats
    try:
        stats = await get_graph_stats(request.app.state.neo4j_graph)
        return stats
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"Graph stats error: {str(e)}")


# =============================================================================
# Full Pipeline (Celery chain: extract → ingest Qdrant → ingest Neo4j)
# =============================================================================
class PipelineRequest(BaseModel):
    """Full channel pipeline: extract → ingest vectors → ingest graph."""
    channel_id: str
    max_results: int = 0
    include_transcription: bool = True
    include_qdrant: bool = True
    include_graph: bool = False


@router.post("/pipeline")
async def full_pipeline(body: PipelineRequest):
    """
    Full channel pipeline (Celery chain).
    Triggers: extract_channel → ingest_to_qdrant → ingest_to_graph → clear_cache

    Each step runs in its own Celery worker queue.
    Returns immediately with task_id.
    """
    from tasks.pipeline import full_channel_pipeline
    task = full_channel_pipeline.delay(
        body.channel_id,
        body.max_results,
        body.include_transcription,
        body.include_qdrant,
        body.include_graph,
    )
    return {"task_id": task.id, "status": "queued", "endpoint": f"/api/v1/tasks/{task.id}"}


# =============================================================================
# Helpers
# =============================================================================
def _ensure_embeddings(app):
    """
    Lazy-load embedding models on first use.
    Avoids OOMKilled at startup when Playwright + embeddings exceed 4Gi.
    Once loaded, cached on app.state for subsequent requests.
    """
    if app.state.dense_embeddings is None:
        from services.embeddings import create_dense_embeddings, create_sparse_embeddings
        app.state.dense_embeddings = create_dense_embeddings()  # NVIDIA NIM API (zero CPU)
        app.state.sparse_embeddings = create_sparse_embeddings()  # Local BM25 (minimal CPU)
        print("Embeddings initialized (NVIDIA NIM API + BM25 sparse)", flush = True)
    return app.state.dense_embeddings


def _build_graph(request: Request):
    """
    Build the LangGraph workflow from app state.

    CONCEPT: SmartRetriever orchestrates THREE retrieval sources:
    1. Qdrant hybrid (dense + sparse) — content/semantic search
    2. Neo4j graph traversal — entity and relationship queries
    3. ES full-text — fallback if both above are unavailable

    Qdrant and Neo4j run in PARALLEL via asyncio.gather.
    Results are merged and deduplicated before grading.
    """
    app = request.app
    # Two-stage retrieval: overfetch from each source → FlashRank reranks to top 10.
    # Each source fetches 15 candidates (wide net), SmartRetriever merges ~30,
    # deduplicates, then FlashRank cross-encoder picks the best 10.
    # Grading further filters to ~5-7 relevant documents for generation.
    RETRIEVER_TOP_K = 15  # Per-source fetch (Qdrant, Neo4j, ES)
    FINAL_TOP_K = 10      # After rerank

    # ES retriever (fallback only — used when Qdrant+Neo4j both fail)
    es_retriever = ElasticsearchRetriever(app.state.es, top_k = RETRIEVER_TOP_K)
    # Qdrant hybrid retriever — lazy-load embeddings on first use
    qdrant_retriever = None
    dense = _ensure_embeddings(app)
    sparse = app.state.sparse_embeddings
    if dense and sparse:
        qdrant_retriever = QdrantHybridRetriever(
            qdrant = app.state.qdrant,
            dense_embeddings = dense,
            sparse_embeddings = sparse,
            top_k = RETRIEVER_TOP_K,
        )
    # Neo4j graph retriever (available after /ingest/graph)
    neo4j_retriever = None
    if hasattr(app.state, "neo4j_graph"):
        neo4j_retriever = Neo4jRetriever(
            neo4j_graph = app.state.neo4j_graph,
            llm = app.state.llm,
            top_k = RETRIEVER_TOP_K,
        )
    # Smart retriever: Qdrant + Neo4j in parallel, ES fallback, rerank to FINAL_TOP_K
    retriever = SmartRetriever(es_retriever, qdrant_retriever, neo4j_retriever, top_k = FINAL_TOP_K)
    grader = DocumentGrader(app.state.llm)
    return build_adaptive_rag_graph(
        retriever = retriever,
        grader = grader,
        llm = app.state.llm,
        checkpointer = app.state.checkpointer,
        neo4j_graph = app.state.neo4j_graph if hasattr(app.state, "neo4j_graph") else None,
    )


def _serialize_update(node_name: str, update: dict) -> dict:
    """Convert a node update to JSON-serializable format."""
    result = {"node": node_name}
    if "documents" in update:
        result["documents"] = [
            {
                "video_id": doc.metadata.get("video_id"),
                "title": doc.metadata.get("title"),
                "source": doc.metadata.get("source"),
                "content_preview": doc.page_content[:200],
            }
            for doc in update["documents"]
        ]
        result["document_count"] = len(update["documents"])
    if "generation" in update:
        result["generation"] = update["generation"]
    if "search_query" in update:
        result["search_query"] = update["search_query"]
    if "retry_count" in update:
        result["retry_count"] = update["retry_count"]
    # Adaptive RAG fields
    if "mode" in update:
        result["mode"] = update["mode"]
    if "sub_questions" in update and update["sub_questions"]:
        result["sub_questions"] = update["sub_questions"]
    if "research_plan" in update and update["research_plan"]:
        result["research_plan"] = update["research_plan"]
    if "sub_results" in update and update["sub_results"]:
        result["sub_results_count"] = len(update["sub_results"])
        # Preview latest sub-result
        latest = update["sub_results"][-1]
        result["latest_sub_question"] = latest.get("sub_question", "")
        result["latest_sub_answer_preview"] = latest.get("answer", "")[:200]
    if "confidence_score" in update and update["confidence_score"]:
        result["confidence_score"] = update["confidence_score"]
    return result
