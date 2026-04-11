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
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from schemas.inputs import LLMConfig, RAGSearchRequest
from services.retriever import (
    ElasticsearchRetriever,
    QdrantHybridRetriever,
    Neo4jRetriever,
    SmartRetriever,
)
from services.grader import DocumentGrader
from agents.youtube import build_youtube_rag_graph


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
    cached = await get_cached_response(request.app.state.redis_aio, body.question)
    if cached:
        cached["_from_cache"] = True
        return cached

    graph = _build_graph(request)
    initial_state = {
        "question": body.question,
        "documents": [],
        "generation": "",
        "retry_count": 0,
        "search_query": body.question,
        "grounded": False,
        "citations": [],
        "retrieval_sources": [],
    }
    config = {
        "configurable": {
            "thread_id": body.thread_id,
            "max_retries": body.max_retries,
        },
        "recursion_limit": (body.max_retries + 1) * 6,  # 6 nodes per cycle now
    }
    try:
        result = await graph.ainvoke(initial_state, config = config)
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"Agent error: {str(e)}")

    response = {
        "answer": result.get("generation", "No answer generated."),
        "citations": result.get("citations", []),
        "grounded": result.get("grounded", False),
        "retrieval_sources": result.get("retrieval_sources", []),
        "retry_count": result.get("retry_count", 0),
        "search_query": result.get("search_query", body.question),
    }

    # Cache the response
    await cache_response(request.app.state.redis_aio, body.question, response)

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
        "documents": [],
        "generation": "",
        "retry_count": 0,
        "search_query": body.question,
    }
    config = {
        "configurable": {
            "thread_id": body.thread_id,
            "max_retries": body.max_retries,
        },
        "recursion_limit": (body.max_retries + 1) * 6,
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
async def ingest_to_qdrant(
    body: IngestRequest,
    request: Request,
    background: bool = Query(False, alias = "async", description = "Run as background task (Celery)"),
):
    """
    Ingest transcripts from ES → Qdrant hybrid collection.

    With ?async=true: returns immediately with task_id (Celery background task).
    Without: blocks until complete (original behavior).

    Flow: ES transcriptions → chunk → embed (dense + sparse) → Qdrant upsert
    """
    if background:
        from tasks.ingestion import ingest_to_qdrant as ingest_task
        task = ingest_task.delay(body.video_ids, body.chunk_size, body.chunk_overlap)
        return {"task_id": task.id, "status": "queued", "endpoint": "/api/v1/tasks/" + task.id}

    from services.ingestion import ingest_to_qdrant as run_ingestion
    from services.cache import invalidate_cache

    try:
        stats = await run_ingestion(
            es = request.app.state.es,
            qdrant = request.app.state.qdrant,
            video_ids = body.video_ids,
            chunk_size = body.chunk_size,
            chunk_overlap = body.chunk_overlap,
        )
        await invalidate_cache(request.app.state.redis_aio)
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"Ingestion error: {str(e)}")
    return stats


# =============================================================================
# Knowledge Graph (Phase 3)
# =============================================================================
class GraphIngestRequest(BaseModel):
    """
    Request to extract entities from transcript chunks into Neo4j.
    If video_ids is None, processes ALL transcripts in ES.
    batch_size controls concurrent LLM calls per batch.
    """
    video_ids: list[str] | None = None
    batch_size: int = 10
    chunk_size: int = 2000
    chunk_overlap: int = 200


@router.post("/ingest/graph")
async def ingest_to_graph(
    body: GraphIngestRequest,
    request: Request,
    background: bool = Query(False, alias = "async", description = "Run as background task (Celery)"),
):
    """
    Extract entities and relationships from transcript chunks into Neo4j.

    With ?async=true: returns immediately with task_id (recommended for large datasets).
    Without: blocks until complete (original behavior).

    COST WARNING: Each chunk = 1 LLM call for entity extraction.
    100 chunks ≈ 100 LLM calls. Use ?async=true for large datasets.
    """
    if background:
        from tasks.graph import ingest_to_graph as graph_task
        task = graph_task.delay(body.video_ids, body.batch_size, body.chunk_size, body.chunk_overlap)
        return {"task_id": task.id, "status": "queued", "endpoint": "/api/v1/tasks/" + task.id}
    from services.ingestion import fetch_transcripts_from_es, fetch_metadata_from_es
    from services.chunker import create_chunker, chunk_transcript
    from services.graph_builder import (
        extract_and_store_graph,
        build_video_metadata_graph,
    )
    app = request.app
    neo4j_graph = app.state.neo4j_graph
    try:
        # 1. Fetch transcripts and metadata from ES
        transcripts = await fetch_transcripts_from_es(app.state.es, body.video_ids)
        if not transcripts:
            return {"error": "No transcripts found in ES"}
        all_video_ids = list({t["video_id"] for t in transcripts})
        metadata_map = await fetch_metadata_from_es(app.state.es, all_video_ids)
        # 2. Create Video/Channel nodes from metadata (no LLM cost)
        video_metadata = [
            {**metadata_map.get(vid, {}), "video_id": vid}
            for vid in all_video_ids
        ]
        build_video_metadata_graph(neo4j_graph, video_metadata)
        # 3. Chunk transcripts
        chunker = create_chunker(body.chunk_size, body.chunk_overlap)
        all_chunks = []
        for transcript in transcripts:
            vid = transcript["video_id"]
            meta = metadata_map.get(vid, {})
            chunks = chunk_transcript(
                video_id = vid,
                content = transcript.get("content", ""),
                metadata = {
                    "title": meta.get("title", ""),
                    "channel": meta.get("channel", ""),
                },
                chunker = chunker,
            )
            all_chunks.extend(chunks)
        # 4. Extract entities via LLM and store in Neo4j
        extraction_stats = await extract_and_store_graph(
            documents = all_chunks,
            llm = app.state.llm,
            neo4j_graph = neo4j_graph,
            batch_size = body.batch_size,
        )
        return {
            "videos_processed": len(all_video_ids),
            "chunks_processed": len(all_chunks),
            **extraction_stats,
        }
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"Graph ingestion error: {str(e)}")


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
        app.state.dense_embeddings = create_dense_embeddings("bge-base")
        app.state.sparse_embeddings = create_sparse_embeddings()
        print("Embedding models lazy-loaded (bge-base ONNX + BM25 sparse)", flush = True)
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
    # ES retriever (always available)
    # top_k=5: with fallback chain (8 models × 40 RPM = ~320 RPM), no rate limit concern
    es_retriever = ElasticsearchRetriever(app.state.es, top_k = 5)
    # Qdrant hybrid retriever — lazy-load embeddings on first use
    qdrant_retriever = None
    dense = _ensure_embeddings(app)
    sparse = app.state.sparse_embeddings
    if dense and sparse:
        qdrant_retriever = QdrantHybridRetriever(
            qdrant = app.state.qdrant,
            dense_embeddings = dense,
            sparse_embeddings = sparse,
            top_k = 5,
        )
    # Neo4j graph retriever (available after /ingest/graph)
    neo4j_retriever = None
    if hasattr(app.state, "neo4j_graph"):
        neo4j_retriever = Neo4jRetriever(
            neo4j_graph = app.state.neo4j_graph,
            llm = app.state.llm,
        )
    # Smart retriever: Qdrant + Neo4j in parallel, ES fallback
    retriever = SmartRetriever(es_retriever, qdrant_retriever, neo4j_retriever)
    grader = DocumentGrader(app.state.llm)
    return build_youtube_rag_graph(
        retriever = retriever,
        grader = grader,
        llm = app.state.llm,
        checkpointer = app.state.checkpointer,
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
    return result
