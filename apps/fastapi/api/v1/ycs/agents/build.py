"""ycs/agents — graph factory + streaming-update serializer.

Direct port of deprecated `routers/v1/youtube/helpers.py:L2014-L2108`.

2026-06-15 — BYOK exclusive-override path REMOVED. Every Ask request
runs through the rotator's full failover chain (`app.state.llm`) so it
gets FGTS-VA bandit + 7-provider failover + EOL detection + cooldowns,
same as Planner and Synth. See `build_graph_from_request`'s docstring
for the rationale.

`build_graph_from_request(request)` returns a freshly-compiled adaptive
RAG graph wired to the rotator chain.

`_serialize_update(node, update)` projects a LangGraph `astream` update
into a JSON-safe dict for SSE delivery."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from domains.ycs.rag.adaptive import build_adaptive_rag_graph


async def build_graph_from_request(request: Request):
    """Build the adaptive RAG graph from FastAPI `app.state` against
    the rotator's full failover chain. **Always** uses
    `app.state.llm` — the FGTS-VA bandit over 7 providers with EOL
    detection, periodic catalog refresh, and runtime cooldowns.

    2026-06-15 — dropped the BYOK exclusive-override path. The earlier
    design let `LLMConfig` substitute a single-model `ChatLiteLLM`
    that bypassed the rotator entirely. That created a single point of
    failure (any 429 / timeout / EOL on the picked model = the whole
    request fails) and threw away every reliability mechanism Planner
    and Synth already prove out. Same workload shape as Ask; same
    rotator path is the right call.

    The BYOK Redis config + `POST /agents/config/test` endpoint are
    kept for a future "preferred arm" feature (user's pick boosted to
    the top of the rotator pool rather than replacing it). Until that
    lands, BYOK is effectively advisory and has no runtime effect.

    Provisioned deps (lifespan):
      - `smart_retriever` — ES + Qdrant + Neo4j fan-out + FlashRank
      - `grader`          — DocumentGrader bound to `app.state.llm`
      - `llm`             — rotator's `with_fallbacks` chain
      - `neo4j_graph`     — LangChain Neo4jGraph wrapper

    `checkpointer=None` matches deprecated."""
    app = request.app
    return build_adaptive_rag_graph(
        retriever    = app.state.smart_retriever,
        grader       = app.state.grader,
        llm          = app.state.llm,
        checkpointer = None,
        neo4j_graph  = app.state.neo4j_graph,
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
