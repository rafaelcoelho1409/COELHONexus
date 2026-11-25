"""Search MCP connection and run-scoped source-read orchestration."""
from __future__ import annotations
import domains
from . import domain, entities, params
from ..retrieval import domain as retrieval_domain

import asyncio
import hashlib
import json
import time
from typing import Any, cast

from fastmcp import Client
from langchain_mcp_adapters.client import MultiServerMCPClient


async def _resolve_remote_reader(session: entities.SearchSession) -> Any | None:
    if session.reader_initialized:
        return session.remote_read_tool
    session.reader_initialized = True

    try:
        connection = domains.settings.search.service.resolve_default_mcp_connection()
    except Exception as error:  # noqa: BLE001 — a settings outage should not fail the agent run.
        session.error_type = type(error).__name__
        return None
    if connection is None:
        session.error_type = "SearchConnectionUnavailable"
        return None
    if session.provider_id and session.provider_id != connection.provider_id:
        session.error_type = "SearchProviderChanged"
        return None

    session.provider_id = connection.provider_id
    try:
        client = MultiServerMCPClient({
            connection.provider_id: {
                "url": connection.endpoint,
                "transport": "streamable_http",
                **({"headers": connection.headers} if connection.headers else {}),
            },
        })
        tools = await asyncio.wait_for(
            client.get_tools(),
            timeout = params.SEARCH_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 — Search MCP outages do not fail the agent run.
        session.error_type = type(error).__name__
        return None

    session.mcp_client = client
    session.remote_tools = tools
    session.remote_read_tool = next(
        (
            item for item in tools
            if "search" not in item.name.casefold()
            and any(token in item.name.casefold() for token in ("fetch", "open_url", "read_url", "fetch_page"))
        ),
        None,
    )
    if session.remote_read_tool is None:
        session.error_type = "SearchReadToolUnavailable"
    return session.remote_read_tool


async def search_mcp_web(session: entities.SearchSession, query: str) -> str:
    """Call BD's bounded Search MCP tool and retain normalized run-scoped sources."""
    normalized_query = " ".join(query.split())[:400].strip()
    if not normalized_query:
        session.status = "invalid_query"
        return json.dumps({"status": "invalid_query", "sources": []})

    async with session.lock:
        cached = session.query_results.get(normalized_query.casefold())
        if cached is not None:
            return json.dumps({
                "status": session.status,
                "provider_id": session.provider_id,
                "query": normalized_query,
                "sources": cached,
            })
        if len(session.queries) >= params.MAX_QUERIES:
            session.status = "query_limit"
            return json.dumps({"status": session.status, "sources": []})

        session.queries.append(normalized_query)
        try:
            from entrypoints.mcp.server import internal_mcp

            async with Client(internal_mcp, timeout = 100) as client:
                result = await client.call_tool("bd_search_web", {"query": normalized_query})
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — one failed query should not fail the agent run.
            session.status = "unavailable" if not session.sources else "complete"
            session.error_type = type(error).__name__
            return json.dumps({
                "status": session.status,
                "provider_id": session.provider_id,
                "error_type": session.error_type,
                "sources": [],
            })

        data = retrieval_domain.to_plain_data(result.data)
        if isinstance(data, dict) and set(data) == {"root"} and isinstance(data["root"], dict):
            data = data["root"]
        if not isinstance(data, dict):
            try:
                data = json.loads(str(data))
            except (TypeError, json.JSONDecodeError):
                data = {"status": "unavailable", "evidence": []}

        result_status = str(data.get("status") or "unavailable")
        if result_status not in {
            "disabled",
            "invalid_query",
            "no_results",
            "complete",
            "query_limit",
            "unavailable",
            "unsupported_provider",
        }:
            result_status = "unavailable"
        error_type = data.get("error_type")
        if isinstance(error_type, str):
            session.error_type = error_type[:100]
        remaining = params.MAX_SOURCES - len(session.sources)
        source_records = domain.sources_from_retrieval_evidence(
            data.get("evidence"),
            query = normalized_query,
            limit = max(remaining, 0),
        ) if remaining > 0 else []
        for source in source_records:
            session.sources.setdefault(source["source_id"], source)
        if source_records:
            session.provider_id = source_records[0]["provider_id"]
        session.status = cast(
            entities.SearchStatus,
            "complete" if session.sources else (
                "no_results" if result_status == "complete" else result_status
            ),
        )
        session.query_results[normalized_query.casefold()] = source_records
        return json.dumps({
            "status": session.status,
            "provider_id": session.provider_id,
            "query": normalized_query,
            "sources": source_records,
        }, ensure_ascii = False)


async def read_search_source(session: entities.SearchSession, source_id: str) -> str:
    """Read a previously returned source by its run-scoped ID and record its verified content hash."""
    normalized_id = source_id.strip()
    async with session.lock:
        source = session.sources.get(normalized_id)
        if source is None:
            return json.dumps({"status": "unknown_source", "source_id": normalized_id})
        cached_text = session.source_texts.get(normalized_id)
        if cached_text is not None:
            return json.dumps({
                "status": "complete",
                "source_id": normalized_id,
                "full_text_sha256": source.get("full_text_sha256"),
                "text": cached_text,
            }, ensure_ascii = False)
        if len(session.read_source_ids) >= params.MAX_SOURCE_READS:
            return json.dumps({"status": "read_limit", "source_id": normalized_id})
        read_tool = await _resolve_remote_reader(session)
        if read_tool is None:
            return json.dumps({
                "status": "full_source_read_unsupported",
                "source_id": normalized_id,
                "error_type": session.error_type,
            })
        arguments = domain.source_read_arguments(
            read_tool,
            str(source.get("url", "")),
            session.run_id,
            params.MAX_SOURCE_TEXT_CHARS,
        )
        if arguments is None:
            return json.dumps({"status": "full_source_read_unsupported", "source_id": normalized_id})
        session.read_source_ids.add(normalized_id)
        try:
            raw = await asyncio.wait_for(
                read_tool.ainvoke(arguments),
                timeout = params.SEARCH_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — source-read failures do not fail the guide.
            return json.dumps({
                "status": "unavailable",
                "source_id": normalized_id,
                "error_type": type(error).__name__,
            })
        text = domain.normalize_source_text(raw, params.MAX_SOURCE_TEXT_CHARS)
        if len(text) < 120:
            return json.dumps({"status": "unavailable", "source_id": normalized_id, "error_type": "InsufficientSourceText"})
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        accessed_at = time.time()
        source.update({
            "full_text_sha256": digest,
            "full_text_char_count": len(text),
            "full_text_accessed_at": accessed_at,
        })
        session.source_texts[normalized_id] = text
        return json.dumps({
            "status": "complete",
            "source_id": normalized_id,
            "title": source.get("title"),
            "url": source.get("url"),
            "full_text_sha256": digest,
            "text": text,
        }, ensure_ascii = False)
