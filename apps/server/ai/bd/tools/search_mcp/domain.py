"""Deterministic query, MCP argument, and external-source normalization rules."""
from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit


def search_arguments(tool: Any, query: str, session_id: str) -> dict[str, Any] | None:
    """Build arguments only for fields declared by a discovered MCP search tool."""
    schema_type = getattr(tool, "args_schema", None)
    try:
        schema = schema_type.model_json_schema() if schema_type is not None else {}
    except Exception:  # noqa: BLE001 — incompatible tool schema is a safe unavailable result.
        return None
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not isinstance(properties, dict) or not isinstance(required, list):
        return None

    arguments: dict[str, Any] = {}
    for name, definition in properties.items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            continue
        lowered = name.casefold()
        kind = definition.get("type")
        if lowered in {"search_queries", "queries"}:
            is_array = kind == "array" or (isinstance(kind, list) and "array" in kind)
            arguments[name] = [query] if is_array else query
        elif lowered in {"query", "search_query", "objective", "q", "question"}:
            arguments[name] = query
        elif lowered in {"session_id", "session", "thread_id"}:
            arguments[name] = session_id[:100]
        elif lowered in {"max_results", "limit", "count", "num_results", "results"}:
            arguments[name] = 4

    if any(field not in arguments for field in required if isinstance(field, str)):
        return None
    return arguments


def source_read_arguments(tool: Any, source_url: str, session_id: str, max_chars: int) -> dict[str, Any] | None:
    """Build a source-read call using only parameters declared by the connected MCP tool."""
    schema_type = getattr(tool, "args_schema", None)
    try:
        schema = schema_type.model_json_schema() if schema_type is not None else {}
    except Exception:  # noqa: BLE001 — incompatible provider tools fail closed.
        return None
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not isinstance(properties, dict) or not isinstance(required, list):
        return None

    arguments: dict[str, Any] = {}
    has_url = False
    for name, definition in properties.items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            continue
        lowered = name.casefold()
        if lowered in {"url", "source_url", "link", "uri", "target_url", "page_url"}:
            arguments[name] = source_url
            has_url = True
        elif lowered in {"max_chars", "max_length", "char_limit", "limit", "max_content_chars"}:
            kind = definition.get("type")
            if kind == "integer" or kind == "number":
                arguments[name] = max_chars
            elif kind == "string":
                arguments[name] = str(max_chars)
        elif lowered in {"session_id", "session", "thread_id"}:
            arguments[name] = session_id[:100]

    if not has_url or any(field not in arguments for field in required if isinstance(field, str)):
        return None
    return arguments


def normalize_source_text(raw: Any, max_chars: int) -> str:
    """Extract bounded readable text from common MCP result containers."""
    fragments: list[str] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 7 or len("\n".join(fragments)) >= max_chars:
            return
        if isinstance(value, str):
            text = value.strip()
            if text.startswith(("{", "[")):
                try:
                    visit(json.loads(text), depth + 1)
                    return
                except ValueError:
                    pass
            if text:
                fragments.append(text)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
            return
        if isinstance(value, dict):
            for key in ("text", "content", "body", "markdown", "page_content", "result", "structuredContent"):
                if key in value:
                    visit(value[key], depth + 1)
            return
        content = getattr(value, "content", None)
        if content is not None:
            visit(content, depth + 1)
        text = getattr(value, "text", None)
        if isinstance(text, str):
            visit(text, depth + 1)
        page_content = getattr(value, "page_content", None)
        if isinstance(page_content, str):
            visit(page_content, depth + 1)

    visit(raw)
    return "\n".join(dict.fromkeys(fragment for fragment in fragments if fragment))[:max_chars]


def normalize_sources(
    raw: Any,
    *,
    provider_id: str,
    query: str,
    accessed_at: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Extract only dated, title-bearing HTTPS references from a tool response."""
    candidates: list[dict[str, Any]] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                return
            visit(decoded, depth + 1)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            metadata = getattr(value, "metadata", None)
            page_content = getattr(value, "page_content", None)
            if isinstance(metadata, dict):
                visit({**metadata, "snippet": page_content}, depth + 1)
            return

        url = value.get("url") or value.get("link") or value.get("source_url")
        title = value.get("title") or value.get("name")
        snippet = (
            value.get("snippet")
            or value.get("description")
            or value.get("content")
            or value.get("text")
            or value.get("excerpts")
        )
        if isinstance(snippet, list):
            snippet = " ".join(item for item in snippet if isinstance(item, str))
        if isinstance(url, str) and isinstance(title, str) and isinstance(snippet, str):
            candidates.append({
                "url": url.strip(),
                "title": title.strip(),
                "snippet": snippet.strip(),
                "publisher": value.get("publisher") or value.get("domain") or value.get("site_name"),
                "published_at": value.get("published_at") or value.get("published_date") or value.get("publish_date") or value.get("date"),
            })
        for key in ("results", "sources", "items", "data", "content", "structuredContent", "text"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)) or (
                isinstance(nested, str) and nested.lstrip().startswith(("{", "["))
            ):
                visit(nested, depth + 1)

    visit(raw)
    sources = []
    seen_urls = set()
    for item in candidates:
        parsed = urlsplit(item["url"])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or item["url"] in seen_urls
            or not item["title"]
            or not item["snippet"]
        ):
            continue
        seen_urls.add(item["url"])
        source_id = hashlib.sha256(f"{provider_id}\0{item['url']}".encode()).hexdigest()[:24]
        sources.append({
            "source_id": source_id,
            "provider_id": provider_id,
            "title": item["title"][:240],
            "url": item["url"][:2048],
            "snippet": item["snippet"][:1200],
            "query": query[:400],
            "publisher": item["publisher"][:160] if isinstance(item["publisher"], str) else None,
            "published_at": item["published_at"][:80] if isinstance(item["published_at"], str) else None,
            "accessed_at": accessed_at,
        })
        if len(sources) >= limit:
            break
    return sources
