"""Retrieval, authorization, and rate-limit orchestration for BD Search MCP."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import ai
import domains
import infra

from fastmcp.server.dependencies import get_access_token
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import domain, params, schemas


logger = logging.getLogger(__name__)


async def authorize_book(book_id: str, trusted_in_process: bool) -> None:
    if not domain.safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if trusted_in_process:
        return
    if params.MCP_PROFILE == "local_demo":
        if not domain.book_is_allowed(book_id, params.DEMO_BOOK_IDS):
            raise PermissionError("This book is not available in the local demo corpus")
        return
    token = get_access_token()
    if token is None:
        raise PermissionError("Authentication is required")
    if book_id not in domain.token_book_ids(token.claims):
        raise PermissionError("The authenticated principal cannot access this book")


async def authorize_web(trusted_in_process: bool) -> None:
    if trusted_in_process:
        return
    token = get_access_token()
    if token is None or params.WEB_SEARCH_SCOPE not in token.scopes:
        raise PermissionError("The authenticated principal lacks the external-search scope")


async def search_store(source: str, book_id: str, run_id: str, query: str) -> schemas.RetrievalResult:
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


async def search_web(query: str, trusted_in_process: bool) -> schemas.RetrievalResult:
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
        client = MultiServerMCPClient({
            connection.provider_id: {
                "url": connection.endpoint,
                "transport": "streamable_http",
                **({"headers": connection.headers} if connection.headers else {}),
            },
        })
        available = await asyncio.wait_for(client.get_tools(), timeout = params.WEB_SEARCH_TIMEOUT_S)
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
        arguments = ai.bd.tools.search.domain.search_arguments(
            search_tool,
            normalized_query,
            f"bd-mcp-{int(time.time())}",
        )
        if arguments is None:
            return schemas.RetrievalResult(
                status = "unavailable", source = "external_search", query = normalized_query,
                evidence = [], error_type = "SearchToolSchemaUnsupported",
            )
        await claim_web_search(trusted_in_process)
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

    sources = ai.bd.tools.search.domain.normalize_sources(
        raw,
        provider_id = connection.provider_id,
        query = normalized_query,
        accessed_at = time.time(),
        limit = params.MAX_WEB_RESULTS,
    )
    evidence = [
        {
            "id": f"external:{item['source_id']}",
            "text": str(item.get("snippet") or "")[:3000],
            "score": float(len(sources) - rank),
            "source": "external_search",
            "citation": {
                "title": str(item.get("title") or "External search source"),
                "url": str(item.get("url") or ""),
                "book_id": "",
                "page": None,
                "chapter": str(item.get("publisher") or ""),
                "section": "External search",
                "snippet": str(item.get("snippet") or "")[:1000],
                "source": "external_search",
                "provider_id": connection.provider_id,
                "published_at": item.get("published_at"),
                "accessed_at": item.get("accessed_at"),
            },
        }
        for rank, item in enumerate(sources)
        if item.get("source_id") and item.get("url")
    ]
    return schemas.RetrievalResult(
        status = "complete" if evidence else "no_results",
        source = "external_search",
        query = normalized_query,
        evidence = evidence,
    )


async def claim_web_search(trusted_in_process: bool) -> None:
    if not infra.redis.service.is_configured():
        raise RuntimeError("Settings-backed search requires Redis-backed request limits")
    token = get_access_token() if not trusted_in_process else None
    principal = (token.subject or token.client_id) if token is not None else "internal-agent"
    principal_hash = hashlib.sha256(principal.encode("utf-8")).hexdigest()[:32]
    minute = int(time.time() // 60)
    key = f"mcp:bd-search:web:{principal_hash}:{minute}"
    async with infra.redis.service.client(
        connect_timeout_s = params.REDIS_TIMEOUT_S,
        op_timeout_s = params.REDIS_TIMEOUT_S,
    ) as redis:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 120)
    if count > params.WEB_SEARCHES_PER_MINUTE:
        raise PermissionError("The external-search request limit has been reached")
