"""Orchestration for source-specific BD internal MCP retrieval calls."""
from __future__ import annotations
import domains
from . import domain, entities, keys, params

import json
from typing import Any

from fastmcp import Client


async def call_source_tool(
    session: entities.RetrievalSession,
    query: str,
    *,
    source: entities.RetrievalSource,
    neo4j_intent: domains.bd.rag.source.retrieval.schemas.Neo4jQueryIntent | None = None,
) -> str:
    """Call the MCP search tool for one source and capture its evidence in the session."""
    if session.source != source:
        raise ValueError(
            f"retrieval session source {session.source!r} does not match tool source {source!r}"
        )
    if neo4j_intent is not None and source != "neo4j":
        raise ValueError("Neo4j query intent can only be used with the Neo4j retrieval tool")
    normalized_query = " ".join(query.split())[:params.MAX_QUERY_CHARS].strip()
    if not normalized_query:
        return json.dumps({"status": "invalid_query", "source": source, "evidence": []})
    async with session.lock:
        if session.tool_calls >= params.MAX_QUERY_CALLS:
            return json.dumps({"status": "call_limit", "source": session.source, "evidence": []})
        session.tool_calls += 1
        session.queries.append(normalized_query)
    tool_name = keys.MCP_TOOL_NAME_BY_SOURCE[source]
    arguments: dict[str, Any] = {"query": normalized_query}
    arguments.update(book_id = session.book_id, run_id = session.run_id)
    if neo4j_intent is not None:
        arguments["intent"] = neo4j_intent.model_dump(mode = "json")
    from entrypoints.mcp.server import mcp

    async with Client(mcp, timeout = 100) as client:
        result = await client.call_tool(tool_name, arguments)
    data = domain.to_plain_data(result.data)
    if isinstance(data, dict) and set(data) == {"root"} and isinstance(data["root"], dict):
        data = data["root"]
    if not isinstance(data, dict):
        try:
            data = json.loads(str(data))
        except (TypeError, json.JSONDecodeError):
            data = {"status": "unavailable", "source": source, "evidence": []}
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


async def read_source_schema(source: str) -> str:
    """Read one curated schema or policy resource before its Distill agent runs."""
    resource_uris = {
        "elasticsearch": "bd://distill/book-retrieval/elasticsearch/schema",
        "qdrant": "bd://distill/book-retrieval/qdrant/schema",
        "neo4j": "bd://distill/book-retrieval/neo4j/schema",
        "web_search": "bd://distill/book-retrieval/web-search/policy",
    }
    try:
        resource_uri = resource_uris[source]
    except KeyError as error:
        raise ValueError(f"Unsupported BD Distill retrieval source: {source}") from error

    from entrypoints.mcp.server import mcp

    async with Client(mcp, timeout = params.MCP_RESOURCE_TIMEOUT_S) as client:
        contents = await client.read_resource(resource_uri)
    text_contents: list[str] = []
    for item in contents:
        content_text = getattr(item, "text", None)
        if isinstance(content_text, str):
            text_contents.append(content_text)
    if not text_contents:
        raise RuntimeError(f"The {source} retrieval resource returned no text")
    rendered = "\n".join(text_contents)
    try:
        parsed = json.loads(rendered)
    except json.JSONDecodeError:
        return rendered
    return json.dumps(parsed, ensure_ascii = False, indent = 2, default = str)
