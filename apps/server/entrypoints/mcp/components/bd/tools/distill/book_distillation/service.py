"""Distill workflow and retrieval orchestration at the MCP boundary."""
from __future__ import annotations
import ai, asyncio, domains, infra
from . import domain, params, schemas

import logging
import time


logger = logging.getLogger(__name__)


async def read_run(book_id: str, run_id: str) -> dict:
    manifest = await domains.bd.distill.ai.service.read_run(book_id, run_id)
    if manifest is None:
        raise FileNotFoundError("Distill run not found")
    return domain.run_summary(manifest).model_dump(mode = "json")


async def read_current_run(book_id: str) -> dict:
    manifest = await domains.bd.distill.ai.service.read_current_run(book_id)
    if manifest is None:
        raise FileNotFoundError("No Distill run exists for this book")
    return domain.run_summary(manifest).model_dump(mode = "json")


async def read_report(book_id: str, run_id: str) -> dict:
    report, _ = await domains.bd.distill.ai.service.read_report(book_id, run_id)
    return report


async def read_retrieval_context(book_id: str, run_id: str) -> dict:
    storage = infra.minio.service.get_storage()
    context = await domains.bd.distill.ai.retrieval_context.service.read_persisted_retrieval_context(
        storage,
        book_id,
        run_id,
    )
    if context is None:
        raise FileNotFoundError("Distill retrieval evidence is not available for this run")
    return context


async def start_run(
    book_id: str,
    idempotency_key: str,
    ingestion_ai_run_id: str | None = None,
) -> dict:
    result = await domains.bd.distill.ai.dispatch.service.start_run(
        book_id,
        idempotency_key = idempotency_key,
        ingestion_ai_run_id = ingestion_ai_run_id,
    )
    return domain.command_result(result).model_dump(mode = "json")


async def resume_run(book_id: str, run_id: str) -> dict:
    result = await domains.bd.distill.ai.dispatch.service.resume_run(book_id, run_id)
    return domain.command_result(result).model_dump(mode = "json")


async def cancel_run(book_id: str, run_id: str) -> dict:
    result = await domains.bd.distill.ai.dispatch.service.cancel_run(book_id, run_id)
    return domain.command_result(result).model_dump(mode = "json")


async def replay_from_node(book_id: str, run_id: str, node_id: str) -> dict:
    result = await domains.bd.distill.ai.dispatch.service.replay_from_node(
        book_id,
        run_id,
        node_id,
    )
    return domain.command_result(result).model_dump(mode = "json")


async def search_store(
    source: str,
    book_id: str,
    run_id: str,
    query: str,
    *,
    neo4j_intent: domains.bd.rag.source.retrieval.schemas.Neo4jQueryIntent | None = None,
) -> schemas.RetrievalResult:
    normalized_query = domain.normalize_query(query, params.MAX_QUERY_CHARS)
    if not normalized_query:
        return schemas.RetrievalResult(
            status = "invalid_query", source = source, query = "", evidence = [],
        )
    try:
        result = await domains.bd.rag.ai.retrieval.service.retrieve_source(
            book_id,
            run_id,
            normalized_query,
            source,
            top_k = params.MAX_TOP_K,
            neo4j_intent = neo4j_intent,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 — do not leak backend details over MCP.
        logger.warning(
            "BD MCP retrieval failed source=%s error_type=%s",
            source,
            type(error).__name__,
        )
        return schemas.RetrievalResult(
            status = "unavailable", source = source, query = normalized_query,
            evidence = [], error_type = type(error).__name__,
        )
    return schemas.RetrievalResult.model_validate(result)


async def search_web(query: str) -> schemas.RetrievalResult:
    normalized_query = domain.normalize_query(query, params.MAX_WEB_QUERY_CHARS)
    if not normalized_query:
        return schemas.RetrievalResult(
            status = "invalid_query", source = "external_search", query = "", evidence = [],
        )
    try:
        connection = domains.settings.search.service.resolve_default_mcp_connection()
    except Exception as error:  # noqa: BLE001 — external search is an optional lane.
        logger.warning("BD MCP Settings search resolution failed error_type=%s", type(error).__name__)
        return schemas.RetrievalResult(
            status = "unavailable", source = "external_search", query = normalized_query,
            evidence = [], error_type = type(error).__name__,
        )
    if connection is None:
        return schemas.RetrievalResult(
            status = "unavailable", source = "external_search", query = normalized_query,
            evidence = [], error_type = "SearchConnectionUnavailable",
        )
    try:
        client = domains.settings.mcp.service.build_langchain_mcp_adapter(
            connection,
            timeout_s = params.WEB_SEARCH_TIMEOUT_S,
        )
        async with client:
            available = await asyncio.wait_for(
                client.list_tools(), timeout = params.WEB_SEARCH_TIMEOUT_S,
            )
        search_tools = [tool for tool in available if "search" in tool.name.casefold()]
        search_tool = next(
            (tool for tool in search_tools if tool.name.endswith("web_search")),
            search_tools[0] if search_tools else None,
        )
        if search_tool is None:
            return schemas.RetrievalResult(
                status = "unavailable", source = "external_search", query = normalized_query,
                evidence = [], error_type = "SearchToolUnavailable",
            )
        arguments = ai.bd.tools.web_search.domain.search_arguments(
            search_tool,
            normalized_query,
            f"bd-mcp-{int(time.time())}",
        )
        if arguments is None:
            return schemas.RetrievalResult(
                status = "unavailable", source = "external_search", query = normalized_query,
                evidence = [], error_type = "SearchToolSchemaUnsupported",
            )
        await claim_web_search()
        raw = await asyncio.wait_for(
            search_tool.ainvoke(arguments), timeout = params.WEB_SEARCH_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 — external search never blocks internal evidence.
        logger.warning("BD MCP Settings search failed error_type=%s", type(error).__name__)
        return schemas.RetrievalResult(
            status = "unavailable", source = "external_search", query = normalized_query,
            evidence = [], error_type = type(error).__name__,
        )

    sources = ai.bd.tools.web_search.domain.normalize_sources(
        raw,
        provider_id = connection.provider_id,
        query = normalized_query,
        accessed_at = time.time(),
        limit = params.MAX_WEB_RESULTS,
    )
    evidence = ai.bd.tools.web_search.domain.to_retrieval_evidence(sources)
    return schemas.RetrievalResult(
        status = "complete" if evidence else "no_results",
        source = "external_search",
        query = normalized_query,
        evidence = evidence,
    )


async def claim_web_search() -> None:
    if not infra.redis.service.is_configured():
        raise RuntimeError("Settings-backed search requires Redis-backed request limits")
    minute = int(time.time() // 60)
    key = f"mcp:bd-search:web:shared:{minute}"
    async with infra.redis.service.client(
        connect_timeout_s = params.REDIS_TIMEOUT_S,
        op_timeout_s = params.REDIS_TIMEOUT_S,
    ) as redis:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 120)
    if count > params.WEB_SEARCHES_PER_MINUTE:
        raise PermissionError("The external-search request limit has been reached")
