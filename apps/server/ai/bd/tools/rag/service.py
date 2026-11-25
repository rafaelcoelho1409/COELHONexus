"""Orchestration for source-specific BD RAG FastMCP calls."""
from __future__ import annotations
from . import domain, entities, keys, params

import json
from typing import Any

from fastmcp import Client


async def call_source_tool(
    session: entities.RetrievalSession,
    query: str,
) -> str:
    """Call the MCP search tool for one source and capture its evidence in the session."""
    normalized_query = " ".join(query.split())[:params.MAX_QUERY_CHARS].strip()
    if not normalized_query:
        return json.dumps({"status": "invalid_query", "source": session.source, "evidence": []})
    async with session.lock:
        if session.tool_calls >= params.MAX_QUERY_CALLS:
            return json.dumps({"status": "call_limit", "source": session.source, "evidence": []})
        session.tool_calls += 1
        session.queries.append(normalized_query)
    tool_name = keys.MCP_TOOL_NAME_BY_SOURCE[session.source]
    arguments: dict[str, Any] = {"query": normalized_query}
    if session.source != "search_mcp":
        arguments.update(book_id = session.book_id, run_id = session.run_id)
    from entrypoints.mcp.server import internal_mcp

    async with Client(internal_mcp, timeout = 100) as client:
        result = await client.call_tool(tool_name, arguments)
    data = domain.to_plain_data(result.data)
    if isinstance(data, dict) and set(data) == {"root"} and isinstance(data["root"], dict):
        data = data["root"]
    if not isinstance(data, dict):
        try:
            data = json.loads(str(data))
        except (TypeError, json.JSONDecodeError):
            data = {"status": "unavailable", "source": session.source, "evidence": []}
    status = str(data.get("status") or "unavailable")
    session.statuses.append(status)
    error_type = data.get("error_type")
    if isinstance(error_type, str):
        session.error_type = error_type[:100]
    evidence = data.get("evidence")
    if isinstance(evidence, list):
        records = [item for item in evidence if isinstance(item, dict) and item.get("id") and item.get("text")]
        if records:
            session.ranked_lists.append(records)
    return json.dumps(data, ensure_ascii = False, default = str)
