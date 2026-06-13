"""ycs/agents — graph factory + streaming-update serializer.

Direct port of deprecated `routers/v1/youtube/helpers.py:L2014-L2108`,
extended 2026-06-11 with BYOK override:

`build_graph_from_request(request)` reads the user's persisted LLM
config from Redis (`coelhonexus:youtube:agents:config`). If a valid
`api_key + model` pair is present, the graph + grader + Neo4j retriever
are rebuilt with that single-model `ChatLiteLLM`; otherwise the request
falls through to the rotator chain shared with DD/synth (`app.state.llm`).

`_serialize_update(node, update)` projects a LangGraph `astream` update
into a JSON-safe dict for SSE delivery."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from domains.ycs.grader.service           import DocumentGrader
from domains.ycs.rag.adaptive             import build_adaptive_rag_graph
from domains.ycs.retriever.neo4j          import Neo4jRetriever
from domains.ycs.retriever.smart          import SmartRetriever

from .byok import build_byok_llm, get_byok_config


logger = logging.getLogger(__name__)


async def build_graph_from_request(request: Request):
    """Build the adaptive RAG graph from FastAPI `app.state`, optionally
    overriding the LLM with the per-user BYOK config.

    Provisioned deps (lifespan):
      - `smart_retriever` — ES + Qdrant + Neo4j fan-out + FlashRank
      - `grader`          — DocumentGrader bound to `app.state.llm`
      - `llm`             — rotator's `with_fallbacks` chain
      - `neo4j_graph`     — LangChain Neo4jGraph wrapper
      - `redis_aio`       — async Redis for the BYOK config

    BYOK rebuild scope: only the components that actually call the LLM
    are rebuilt — `DocumentGrader` and `Neo4jRetriever`. ES + Qdrant
    retrievers are LLM-free, so they're reused as-is. SmartRetriever is
    rebuilt because it composes the (now overridden) Neo4j arm.

    `checkpointer=None` matches deprecated."""
    app = request.app
    byok_llm = await _resolve_byok_llm(app)
    llm = byok_llm or app.state.llm
    grader = (
        DocumentGrader(byok_llm)
        if byok_llm is not None and app.state.grader is not None
        else app.state.grader
    )
    smart_retriever = (
        _rebuild_smart_with_llm(app.state.smart_retriever, byok_llm, app.state.neo4j_graph)
        if byok_llm is not None and app.state.smart_retriever is not None
        else app.state.smart_retriever
    )
    return build_adaptive_rag_graph(
        retriever    = smart_retriever,
        grader       = grader,
        llm          = llm,
        checkpointer = None,
        neo4j_graph  = app.state.neo4j_graph,
    )


async def _resolve_byok_llm(app):
    """Read Redis BYOK config and build the single-model LLM. Any failure
    (Redis miss, missing api_key, ChatLiteLLM construction error) returns
    `None` so the caller falls back to the rotator chain silently."""
    redis_aio = getattr(app.state, "redis_aio", None)
    if redis_aio is None:
        return None
    config = await get_byok_config(redis_aio)
    if not config:
        return None
    try:
        return build_byok_llm(config)
    except Exception as e:
        logger.warning(
            f"[ycs:byok] ChatLiteLLM build failed: {type(e).__name__}: {e}"
        )
        return None


def _rebuild_smart_with_llm(
    base: SmartRetriever, llm, neo4j_graph,
) -> SmartRetriever:
    """Clone `SmartRetriever` with a new `Neo4jRetriever` bound to the
    BYOK LLM. ES + Qdrant arms are reused — they don't take an LLM."""
    neo4j_retriever = (
        Neo4jRetriever(neo4j_graph = neo4j_graph, llm = llm)
        if neo4j_graph is not None
        else None
    )
    return SmartRetriever(
        es_retriever     = base.es_retriever,
        qdrant_retriever = base.qdrant_retriever,
        neo4j_retriever  = neo4j_retriever,
        use_reranker     = base.use_reranker,
        top_k            = base.top_k,
    )


def _serialize_update(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
    """Project a LangGraph `astream(stream_mode='updates')` patch into a
    JSON-safe dict for SSE delivery.

    Direct port of deprecated `helpers.py:L2073-2108`. Document objects
    are slugged (video_id + title + preview); long generations pass through
    as-is."""
    result: dict[str, Any] = {"node": node_name}
    if "documents" in update:
        documents = update["documents"] or []
        result["documents"] = [
            {
                "video_id":        doc.metadata.get("video_id"),
                "title":           doc.metadata.get("title"),
                "source":          doc.metadata.get("source"),
                "content_preview": doc.page_content[:200],
            }
            for doc in documents
        ]
        result["document_count"] = len(documents)
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
        latest = update["sub_results"][-1]
        result["latest_sub_question"]      = latest.get("sub_question", "")
        result["latest_sub_answer_preview"] = latest.get("answer", "")[:200]
    if "confidence_score" in update and update["confidence_score"]:
        result["confidence_score"] = update["confidence_score"]
    return result
