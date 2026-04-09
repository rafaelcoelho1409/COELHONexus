"""
YouTube Agentic RAG Router

Endpoints:
- PUT  /config         — Update LLM configuration
- POST /search         — Agentic RAG search (full invoke, returns final answer)
- POST /search/stream  — Agentic RAG search with SSE streaming (node-by-node updates)
"""
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from schemas.inputs import LLMConfig, RAGSearchRequest
from services.retriever import ElasticsearchRetriever
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
    Returns the final answer with source documents.

    CONCEPT: ainvoke() runs the entire graph to completion.
    The config dict contains:
    - thread_id: for conversation persistence via the checkpointer
    - recursion_limit: max graph steps (prevents infinite loops)
    """
    graph = _build_graph(request)

    # Initial state
    initial_state = {
        "question": body.question,
        "documents": [],
        "generation": "",
        "retry_count": 0,
        "search_query": body.question,
        "_max_retries": body.max_retries,
    }

    # Run the graph
    # recursion_limit in config, NOT compile() — LangGraph 1.1 breaking change
    config = {
        "configurable": {"thread_id": body.thread_id},
        "recursion_limit": (body.max_retries + 1) * 4,  # 4 nodes per cycle
    }

    try:
        result = await graph.ainvoke(initial_state, config=config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")

    # Format response
    sources = [
        {
            "video_id": doc.metadata.get("video_id"),
            "title": doc.metadata.get("title"),
            "channel": doc.metadata.get("channel"),
            "url": doc.metadata.get("webpage_url"),
            "score": doc.metadata.get("score"),
        }
        for doc in result.get("documents", [])
    ]

    return {
        "answer": result.get("generation", "No answer generated."),
        "sources": sources,
        "retry_count": result.get("retry_count", 0),
        "search_query": result.get("search_query", body.question),
    }


@router.post("/search/stream")
async def rag_search_stream(body: RAGSearchRequest, request: Request):
    """
    Streaming Agentic RAG search via Server-Sent Events (SSE).

    CONCEPT: astream() yields updates as each node completes.
    version="v2" gives type-safe chunks with node name and state updates.
    The client receives real-time progress: which node is running, partial results, etc.
    """
    graph = _build_graph(request)

    initial_state = {
        "question": body.question,
        "documents": [],
        "generation": "",
        "retry_count": 0,
        "search_query": body.question,
        "_max_retries": body.max_retries,
    }

    config = {
        "configurable": {"thread_id": body.thread_id},
        "recursion_limit": (body.max_retries + 1) * 4,
    }

    async def event_generator():
        try:
            async for event in graph.astream(initial_state, config=config, stream_mode="updates"):
                # Each event is a dict: {node_name: state_update}
                for node_name, update in event.items():
                    # Serialize documents for JSON
                    serializable_update = _serialize_update(node_name, update)
                    yield f"data: {json.dumps(serializable_update)}\n\n"
            yield f"data: {json.dumps({'node': 'end', 'status': 'complete'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'node': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# =============================================================================
# Helpers
# =============================================================================
def _build_graph(request: Request):
    """Build the LangGraph workflow from app state."""
    retriever = ElasticsearchRetriever(request.app.state.es)
    grader = DocumentGrader(request.app.state.llm)
    return build_youtube_rag_graph(
        retriever=retriever,
        grader=grader,
        llm=request.app.state.llm,
        checkpointer=request.app.state.checkpointer,
    )


def _serialize_update(node_name: str, update: dict) -> dict:
    """Convert a node update to JSON-serializable format."""
    result = {"node": node_name}
    if "documents" in update:
        result["documents"] = [
            {
                "video_id": doc.metadata.get("video_id"),
                "title": doc.metadata.get("title"),
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
