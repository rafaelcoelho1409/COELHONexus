"""One-shot BD Ask invocation shared with the HTTP adaptive graph."""

from __future__ import annotations

import logging
import time
import uuid

import ai
import domains
import infra

from ... import ingestion
from . import domain, params, schemas

logger = logging.getLogger(__name__)


async def ask(
    book_id: str,
    question: str,
    *,
    mode: schemas.AskMode | None = None,
    sub_questions: list[str] | None = None,
) -> schemas.AskResult:
    if not domains.bd.identifiers.domain.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    normalized_question = question.strip()
    if not normalized_question or len(normalized_question) > params.MAX_QUESTION_CHARS:
        raise ValueError("question must contain between 1 and 8000 characters")
    normalized_sub_questions = [item.strip() for item in sub_questions or []]
    if len(normalized_sub_questions) > params.MAX_SUB_QUESTIONS or any(
        not item or len(item) > params.MAX_SUB_QUESTION_CHARS
        for item in normalized_sub_questions
    ):
        raise ValueError(
            "sub_questions must contain at most four non-empty questions of at most 1000 characters"
        )

    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout=120,
            blocking_timeout=30,
        ),
    ):
        current_index = await domains.bd.rag.ai.runs.service.read_current_index(book_id)
    if current_index is None:
        raise FileNotFoundError(
            "This book has no completed Ingestion retrieval index to ask about"
        )

    indexed_run_id = current_index["agentic_run_id"]
    counter_run_id = f"mcp-{uuid.uuid4().hex}"
    try:
        model = domains.settings.chat.service.build_chat_model(timeout_s=600.0)
    except Exception as error:
        logger.exception("[bd:mcp-ask] configured chat model is unavailable")
        raise RuntimeError("The configured chat endpoint is unavailable") from error
    try:
        runtime = domains.bd.ask.ai.service.build_runtime(
            book_id, indexed_run_id, counter_run_id=counter_run_id, model=model
        )
    except Exception as error:
        logger.exception("[bd:mcp-ask] could not construct the adaptive Ask graph")
        raise RuntimeError("The BD Ask graph could not be constructed") from error

    initial_state = domains.bd.ask.ai.service.initial_state(
        question=normalized_question,
        thread_id=counter_run_id,
        turn_id=uuid.uuid4().hex,
        force_mode=mode,
        sub_questions=normalized_sub_questions,
    )
    domains.settings.llm_counter.service.set_context(ns="bd", run_id=counter_run_id)
    started = time.monotonic()
    config = {
        "configurable": {"thread_id": counter_run_id},
        "recursion_limit": ai.bd.graphs.ask.adaptive.params.RECURSION_LIMIT,
        "callbacks": [
            callback
            for callback in (infra.langfuse.service.build_langchain_callback(),)
            if callback is not None
        ],
    }
    try:
        result = await runtime.graph.ainvoke(
            initial_state,
            context=runtime.context,
            config=config,
        )
        if not isinstance(result, dict):
            raise TypeError("the adaptive Ask graph returned an invalid result")
        source_statuses = result.get("retrieval_source_status") or {}
        statuses = (
            {
                source: value.get("status", "unknown")
                for source, value in source_statuses.items()
                if isinstance(source, str) and isinstance(value, dict)
            }
            if isinstance(source_statuses, dict)
            else {}
        )
        usage = await domains.settings.llm_counter.service.read_counters(
            "bd", counter_run_id
        )
        return schemas.AskResult(
            book_id=book_id,
            indexed_run_id=indexed_run_id,
            answer=str(result.get("generation") or ""),
            mode=result.get("mode")
            if result.get("mode") in {"fast", "standard", "deep"}
            else "standard",
            citations=result.get("citations")
            if isinstance(result.get("citations"), list)
            else [],
            retrieval_sources=result.get("retrieval_sources")
            if isinstance(result.get("retrieval_sources"), list)
            else [],
            retrieval_source_status=statuses,
            external_search_status=result.get("external_search_status"),
            external_search_provider=result.get("external_search_provider"),
            confidence_score=result.get("confidence_score"),
            verification_status=result.get("verification_status"),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
        )
    finally:
        domains.settings.llm_counter.service.clear_context()


async def read_readiness(book_id: str) -> dict:
    """Read a consistent snapshot of extraction, Ingestion, and RAG index state."""
    if not domains.bd.identifiers.domain.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            domains.bd.ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout=params.READINESS_TIMEOUT_S,
            blocking_timeout=5,
        ),
    ):
        return await _read_readiness_locked(book_id)


async def _read_readiness_locked(book_id: str) -> dict:
    storage = infra.minio.service.get_storage()
    source_manifest = await domains.bd.ingestion.source.storage.service.read_manifest(
        storage, book_id
    )
    ingestion_manifest = (
        await domains.bd.ingestion.ai.service.read_current_ingestion_ai_run(
            book_id,
            storage=storage,
        )
    )
    current = await domains.bd.rag.ai.runs.service.read_current_index(
        book_id, storage=storage
    )

    ingestion_summary = None
    if ingestion_manifest is not None:
        ingestion_summary = ingestion.book_processing.domain.run_summary(
            ingestion_manifest
        ).model_dump(mode="json")

    current_summary = None
    index_manifest = None
    if current is not None:
        current_summary = ingestion.rag_indexing.domain.current_index_summary(
            current
        ).model_dump(mode="json")
    if ingestion_manifest is not None:
        index_manifest = await domains.bd.rag.ai.runs.service.read_index(
            book_id,
            ingestion_manifest["agentic_run_id"],
            storage=storage,
        )
    if index_manifest is None and current is not None:
        index_manifest = current.get("index")

    index_summary = None
    if index_manifest is not None:
        index_summary = ingestion.rag_indexing.domain.index_summary(
            index_manifest
        ).model_dump(mode="json")

    timestamps = [
        value
        for value in (
            source_manifest.get("updated_at")
            if isinstance(source_manifest, dict)
            else None,
            ingestion_manifest.get("updated_at")
            if isinstance(ingestion_manifest, dict)
            else None,
            index_manifest.get("updated_at")
            if isinstance(index_manifest, dict)
            else None,
        )
        if isinstance(value, (float, int))
    ]
    result = domain.readiness(
        book_id=book_id,
        extraction_status=source_manifest.get("status")
        if isinstance(source_manifest, dict)
        else None,
        extraction_run_id=source_manifest.get("extraction_id")
        if isinstance(source_manifest, dict)
        else None,
        ingestion_run=ingestion_summary,
        index_run=index_summary,
        current_index=current_summary,
        configured_embedding_model=domains.settings.embeddings.service.get_configured_model(),
        updated_at=max(timestamps) if timestamps else None,
    )
    return result.model_dump(mode="json")
