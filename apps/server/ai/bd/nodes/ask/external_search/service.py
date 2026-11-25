"""Bounded external evidence retrieval through the user's selected Search MCP."""
from __future__ import annotations
import domains
from . import params
from .... import graphs, tools

import asyncio
import time

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.runtime import Runtime


async def search_external_evidence(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Search one explicit query and convert valid results to cited evidence records."""
    documents = list(state.get("documents") or [])
    sources = list(state.get("retrieval_sources") or [])
    if not state.get("use_external_search"):
        return {"external_search_status": "not_requested"}

    prior_status = state.get("external_search_status")
    cached_external = list(state.get("external_documents") or [])
    if prior_status and prior_status != "not_requested":
        combined = _combine_documents(documents, cached_external)
        return {
            "documents": combined,
            "document_count": len(combined),
            "retrieval_sources": sorted(set(sources) | ({"external_search"} if cached_external else set())),
        }

    query = " ".join(str(state.get("external_search_query") or state.get("question") or "").split())
    query = query[:params.MAX_QUERY_CHARS]
    if not query:
        return {"external_search_status": "invalid_query"}

    try:
        connection = domains.settings.search.service.resolve_default_mcp_connection()
    except Exception as error:  # noqa: BLE001 — optional external search cannot break book retrieval.
        return {
            "external_search_status": "unavailable",
            "external_search_error": type(error).__name__,
        }
    if connection is None:
        return {"external_search_status": "unavailable"}

    client = MultiServerMCPClient({
        connection.provider_id: {
            "url": connection.endpoint,
            "transport": "streamable_http",
            **({"headers": connection.headers} if connection.headers else {}),
        },
    })
    try:
        available_tools = await asyncio.wait_for(
            client.get_tools(), timeout = params.SEARCH_TIMEOUT_S,
        )
        search_tools = [item for item in available_tools if "search" in item.name.casefold()]
        read_tool = next((
            item for item in available_tools
            if item not in search_tools
            and any(token in item.name.casefold() for token in ("fetch", "open_url", "read_url", "fetch_page"))
        ), None)
        search_tool = next(
            (item for item in search_tools if item.name.endswith("web_search")),
            search_tools[0] if search_tools else None,
        )
        if search_tool is None:
            return {
                "external_search_status": "unavailable",
                "external_search_provider": connection.provider_id,
                "external_search_error": "SearchToolUnavailable",
            }
        arguments = tools.search.domain.search_arguments(
            search_tool,
            query,
            str(state.get("turn_id") or state.get("thread_id") or "bd-ask")[:100],
        )
        if arguments is None:
            return {
                "external_search_status": "unavailable",
                "external_search_provider": connection.provider_id,
                "external_search_error": "SearchToolSchemaUnsupported",
            }
        raw = await asyncio.wait_for(
            search_tool.ainvoke(arguments), timeout = params.SEARCH_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 — internal evidence remains available if this server is down.
        return {
            "external_search_status": "unavailable",
            "external_search_provider": connection.provider_id,
            "external_search_error": type(error).__name__,
        }

    normalized = tools.search.domain.normalize_sources(
        raw,
        provider_id = connection.provider_id,
        query = query,
        accessed_at = time.time(),
        limit = params.MAX_SOURCES,
    )
    source_records = normalized[:params.MAX_EVIDENCE_SOURCES]

    async def _read_source(source: dict) -> tuple[str, bool]:
        if read_tool is None:
            return source["snippet"], False
        arguments = tools.search.domain.source_read_arguments(
            read_tool,
            source["url"],
            str(state.get("turn_id") or state.get("thread_id") or "bd-ask")[:100],
            params.MAX_SOURCE_CHARS,
        )
        if arguments is None:
            return source["snippet"], False
        try:
            raw_source = await asyncio.wait_for(
                read_tool.ainvoke(arguments), timeout = params.SEARCH_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return source["snippet"], False
        full_text = tools.search.domain.normalize_source_text(raw_source, params.MAX_SOURCE_CHARS)
        return (full_text, True) if len(full_text) >= 120 else (source["snippet"], False)

    read_results = await asyncio.gather(*(_read_source(source) for source in source_records))
    external_documents = []
    for source, (evidence_text, source_read) in zip(source_records, read_results, strict = True):
        citation = {
            "title": source["title"],
            "url": source["url"],
            "book_id": runtime.context.book_id,
            "page": None,
            "chapter": source.get("publisher") or "",
            "section": "External search",
            "snippet": source["snippet"],
            "source": "external_search",
            "provider_id": source["provider_id"],
            "published_at": source.get("published_at"),
            "source_read": source_read,
        }
        external_documents.append({
            "id": f"external:{source['source_id']}",
            "text": evidence_text,
            "score": 0.0,
            "source": "external_search",
            "citation": citation,
        })

    combined = _combine_documents(documents, external_documents)
    return {
        "documents": combined,
        "document_count": len(combined),
        "retrieval_sources": sorted(set(sources) | ({"external_search"} if external_documents else set())),
        "external_search_status": "complete" if external_documents else "no_results",
        "external_search_provider": connection.provider_id,
        "external_search_query": query,
        "external_documents": external_documents,
    }


def _combine_documents(documents: list[dict], external_documents: list[dict]) -> list[dict]:
    """Keep a bounded mixed evidence set while reserving room for external sources."""
    book_documents = documents[:12] if external_documents else documents[:20]
    unique = {str(item.get("id")): item for item in book_documents if item.get("id")}
    for item in external_documents:
        if item.get("id"):
            unique.setdefault(str(item["id"]), item)
    return list(unique.values())[:20]
