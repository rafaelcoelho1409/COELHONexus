"""One-shot YCS Ask invocation using the API's adaptive graph and retrievers."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

import ai
import domains
import infra

from . import params, schemas

logger = logging.getLogger(__name__)


async def ask(
    question: str,
    *,
    mode: schemas.AskMode | None = None,
    channel_ids: list[str] | None = None,
    sub_questions: list[str] | None = None,
) -> schemas.AskResult:
    normalized_question = question.strip()
    if not normalized_question or len(normalized_question) > params.MAX_QUESTION_CHARS:
        raise ValueError(
            f"question must contain between 1 and {params.MAX_QUESTION_CHARS} characters"
        )
    normalized_channels = [item.strip() for item in channel_ids or []]
    if len(normalized_channels) > params.MAX_CHANNEL_IDS or any(
        not item or len(item) > params.MAX_CHANNEL_ID_CHARS
        for item in normalized_channels
    ):
        raise ValueError("channel_ids contains an empty or oversized channel ID")
    normalized_sub_questions = [item.strip() for item in sub_questions or []]
    if len(normalized_sub_questions) > params.MAX_SUB_QUESTIONS or any(
        not item or len(item) > params.MAX_SUB_QUESTION_CHARS
        for item in normalized_sub_questions
    ):
        raise ValueError("sub_questions contains an empty or oversized research question")

    try:
        llm = domains.settings.chat.service.build_chat_model(timeout_s=600.0)
    except Exception as error:
        logger.exception("[ycs:mcp-ask] configured chat model is unavailable")
        raise RuntimeError("The configured chat endpoint is unavailable") from error
    try:
        llm_fast = domains.settings.chat.service.build_chat_model(
            timeout_s=120.0,
            max_tokens=350,
        )
    except Exception:
        llm_fast = None

    try:
        es = infra.elasticsearch.service.get_es()
        qdrant = infra.qdrant.service.get_qdrant()
        es_retriever = domains.ycs.ask.retriever.service.ElasticsearchRetriever(es)
        qdrant_retriever = domains.ycs.ask.retriever.service.QdrantHybridRetriever(
            qdrant=qdrant,
            dense_embeddings=domains.ycs.ingestion.embeddings.service.create_dense_embeddings(),
            sparse_embeddings=domains.ycs.ingestion.embeddings.service.create_sparse_embeddings(),
            es_client=es,
        )
        try:
            neo4j_graph = infra.neo4j.service.get_graph()
        except Exception:
            neo4j_graph = None
        neo4j_retriever = (
            domains.ycs.ask.retriever.service.Neo4jRetriever(
                neo4j_graph=neo4j_graph,
                llm=llm,
            )
            if neo4j_graph is not None
            else None
        )
        retriever = domains.ycs.ask.retriever.service.SmartRetriever(
            es_retriever=es_retriever,
            qdrant_retriever=qdrant_retriever,
            neo4j_retriever=neo4j_retriever,
        )
        grader = domains.ycs.ask.grader.service.DocumentGrader(llm)
        graph = ai.ycs.graphs.ask.adaptive.graphs.adaptive_rag_graph(
            retriever=retriever,
            grader=grader,
            llm=llm,
            checkpointer=None,
            neo4j_graph=neo4j_graph,
            llm_fast=llm_fast,
        )
    except Exception as error:
        logger.exception("[ycs:mcp-ask] could not construct the adaptive Ask graph")
        raise RuntimeError("The YCS Ask graph could not be constructed") from error

    run_id = f"mcp-{uuid.uuid4().hex}"
    initial_state = {
        "question": normalized_question,
        "thread_id": run_id,
        "route": "search",
        "contextualized": False,
        "intent": "",
        "mode": "",
        "force_mode": mode or "",
        "conversation_history": [],
        "channel_ids": normalized_channels,
        "generation": "",
        "citations": [],
        "grounded": False,
        "retrieval_sources": [],
        "retry_count": 0,
        "search_query": normalized_question,
        "sub_questions": normalized_sub_questions,
        "sub_results": [],
        "research_plan": "",
        "confidence_score": 0.0,
    }
    config = {
        "configurable": {"thread_id": run_id, "max_retries": 3},
        "recursion_limit": 100,
        "callbacks": [
            callback
            for callback in (infra.langfuse.service.build_langchain_callback(),)
            if callback is not None
        ],
    }
    timeout_s = params.ASK_DEADLINE_S.get(mode or "", params.ASK_DEADLINE_DEFAULT_S)

    domains.settings.llm_counter.service.set_context(ns="ycs", run_id=run_id)
    started = time.monotonic()
    deadline_hit = False
    try:
        with infra.langfuse.service.session(
            "ycs",
            session_id=run_id,
            user_id=(normalized_channels or ["default"])[0],
            channel_id=(normalized_channels or ["default"])[0],
        ), infra.opentelemetry.service.get_tracer().start_as_current_span(
            "ycs.ask.mcp",
            attributes={
                infra.langfuse.keys.KEEP: True,
                infra.langfuse.keys.KIND: "workflow_root",
                infra.langfuse.keys.TRACE_NAME: "ycs.ask.mcp",
                "ycs.route": "search",
                "ycs.thread_id": run_id,
                "ycs.question": normalized_question[:200],
                "ycs.force_mode": mode or "",
                "ycs.channel_count": len(normalized_channels),
                infra.langfuse.keys.OBS_META_WORKFLOW: "ycs_ask",
            },
        ):
            try:
                result = await asyncio.wait_for(
                    graph.ainvoke(initial_state, config=config),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                deadline_hit = True
                fallback = await asyncio.wait_for(
                    domains.ycs.ask.rag.standard.nodes.fallback_answer.node.fallback_answer(
                        {
                            "question": normalized_question,
                            "conversation_history": [],
                            "pre_grade_documents": [],
                            "documents": [],
                        },
                        llm,
                    ),
                    timeout=70.0,
                )
                result = {
                    "generation": fallback.get("generation", ""),
                    "mode": mode or "standard",
                    "citations": fallback.get("citations", []),
                    "grounded": False,
                    "retrieval_sources": [],
                    "retry_count": 0,
                    "search_query": normalized_question,
                    "sub_questions": normalized_sub_questions,
                    "confidence_score": 0.0,
                }
        if not isinstance(result, dict):
            raise TypeError("the adaptive YCS Ask graph returned an invalid result")
        try:
            usage = await domains.settings.llm_counter.service.read_counters("ycs", run_id)
        except Exception:
            usage = {"total": {}, "by_model": {}}
        selected_mode = result.get("mode")
        ask_result = schemas.AskResult(
            answer=str(result.get("generation") or ""),
            mode=selected_mode if selected_mode in {"fast", "standard", "deep"} else "standard",
            citations=result.get("citations") if isinstance(result.get("citations"), list) else [],
            grounded=bool(result.get("grounded")),
            retrieval_sources=(
                result.get("retrieval_sources")
                if isinstance(result.get("retrieval_sources"), list)
                else []
            ),
            retry_count=max(0, int(result.get("retry_count") or 0)),
            search_query=str(result.get("search_query") or normalized_question),
            sub_questions=(
                result.get("sub_questions")
                if isinstance(result.get("sub_questions"), list)
                else []
            ),
            confidence_score=result.get("confidence_score"),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
        )
        domains.ycs.ask.observability.service.record_ask_run(
            route="search",
            mode=ask_result.mode,
            outcome="deadline" if deadline_hit else "done",
            grounded=ask_result.grounded,
            duration_s=max(time.monotonic() - started, 0.0),
            citation_count=len(ask_result.citations),
        )
        return ask_result
    except Exception:
        domains.ycs.ask.observability.service.record_ask_run(
            route="search",
            mode=mode or "unknown",
            outcome="error",
            grounded=False,
        )
        raise
    finally:
        domains.settings.llm_counter.service.clear_context()
