"""YCS Query FastMCP adapters over the existing query and DeepAgent services."""
from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime
from typing import Any

import domains

from . import params, schemas


def _fingerprint(schema_data: dict[str, Any]) -> str:
    encoded = json.dumps(
        schema_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _max_limit(backend: str) -> int:
    return {
        "elasticsearch": params.ES_MAX_SIZE,
        "qdrant": params.QDRANT_MAX_LIMIT,
        "neo4j": params.NEO4J_MAX_LIMIT,
    }[backend]


def backend_catalog() -> schemas.QueryBackendCatalog:
    query = domains.ycs.query.source
    matrix = query.service.namespaces()["matrix"][query.params.APP_YCS]
    descriptions = {
        "elasticsearch": (
            "Elasticsearch JSON search body",
            "YCS video metadata and canonical transcripts",
            query.params.DEFAULT_LIMIT,
        ),
        "qdrant": (
            "Qdrant read-operation JSON body",
            "YCS transcript chunks in the configured hybrid dense/sparse collection",
            query.params.QDRANT_DEFAULT_LIMIT,
        ),
        "neo4j": (
            "Read-only Cypher with an explicit numeric LIMIT",
            "YCS Video, Channel, Document, and __Entity__ graph projection",
            20,
        ),
    }
    return schemas.QueryBackendCatalog(
        backends=[
            schemas.QueryBackendDescription(
                backend=backend,
                label=matrix[backend]["label"],
                query_format=descriptions[backend][0],
                scope=descriptions[backend][1],
                default_limit=descriptions[backend][2],
                maximum_limit=_max_limit(backend),
            )
            for backend in query.params.BACKENDS
        ],
    )


async def read_schema(backend: str) -> schemas.QuerySchemaSnapshot:
    query = domains.ycs.query.source
    if backend not in query.params.BACKENDS:
        raise ValueError(f"Unsupported YCS Query backend: {backend}.")
    cached_schema = await query.service.query_schema(backend, request=None)
    schema_data = {key: value for key, value in cached_schema.items() if key != "cached_at"}
    namespace = query.service.namespaces()["matrix"][query.params.APP_YCS][backend]
    if backend == query.params.BACKEND_ES:
        indices = schema_data.get("indices", {})
        live_data = any(
            isinstance(item, dict) and _as_count(item.get("doc_count")) > 0
            for item in indices.values()
        ) if isinstance(indices, dict) else False
        live_schema = bool(indices) and all(
            isinstance(item, dict) and "error" not in item
            for item in indices.values()
        ) if isinstance(indices, dict) else False
    elif backend == query.params.BACKEND_QDRANT:
        collections = schema_data.get("collections", [])
        live_data = any(
            isinstance(item, dict) and _as_count(item.get("points_count")) > 0
            for item in collections
        ) if isinstance(collections, list) else False
        live_schema = bool(collections) and all(
            isinstance(item, dict) and "error" not in item
            for item in collections
        ) if isinstance(collections, list) else False
    else:
        node_samples = schema_data.get("node_samples") or {}
        live_data = (
            any(bool(samples) for samples in node_samples.values())
            if isinstance(node_samples, dict)
            else False
        )
        live_schema = "error" not in schema_data
    return schemas.QuerySchemaSnapshot(
        backend=backend,
        label=namespace["label"],
        target=namespace["target"],
        live_schema_available=live_schema,
        live_data_available=live_data,
        generated_at=datetime.now(UTC).isoformat(),
        schema_fingerprint=_fingerprint(schema_data),
        schema_data=schema_data,
    )


def _result_limit_error(backend: str, query_text: str) -> str | None:
    source = domains.ycs.query.source
    maximum = _max_limit(backend)
    try:
        if backend == source.params.BACKEND_ES:
            parsed = source.domain.parse_es_body(query_text)
            requested = int(parsed.body["size"])
            raw_offset = parsed.body.get("from", 0)
            if isinstance(raw_offset, bool) or not isinstance(raw_offset, int):
                return "Elasticsearch `from` must be an integer."
            offset = raw_offset
            if offset < 0:
                return "Elasticsearch `from` must be zero or greater."
            if offset > 10_000:
                return "Elasticsearch `from` offset exceeds the YCS MCP maximum of 10000."
        elif backend == source.params.BACKEND_QDRANT:
            parsed = source.domain.parse_qdrant_body(query_text)
            if parsed.op == "count":
                return None
            requested = int(parsed.body["limit"])
        else:
            stripped = source.domain.patterns.CYPHER_STRING.sub("", query_text)
            stripped = source.domain.patterns.CYPHER_LINE_COMMENT.sub("", stripped)
            stripped = source.domain.patterns.CYPHER_BLOCK_COMMENT.sub("", stripped)
            limits = source.domain.patterns.CYPHER_LIMIT_VALUE.findall(stripped)
            if not limits:
                return "Neo4j queries must include an explicit numeric LIMIT."
            requested = max(int(value) for value in limits)
    except Exception:
        return None  # The source validator supplies the syntax/safety error.
    if requested > maximum:
        return f"Requested result limit {requested} exceeds the YCS MCP maximum of {maximum}."
    return None


def _as_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


async def _validate(backend: str, query_text: str) -> tuple[bool, str | None, str]:
    query = domains.ycs.query.source
    cached_schema = await query.service.query_schema(backend, request=None)
    schema = {key: value for key, value in cached_schema.items() if key != "cached_at"}
    valid, error = await query.service.validate_query(backend, query_text, schema=schema)
    if valid:
        error = _result_limit_error(backend, query_text)
        valid = error is None
    return valid, error, _fingerprint(schema)


async def draft_query(
    backend: str,
    user_request: str,
    previous_query: str = "",
) -> schemas.QueryDraft:
    query = domains.ycs.query.source
    if backend not in query.params.BACKENDS:
        raise ValueError(f"Unsupported YCS Query backend: {backend}.")
    prompt = user_request.strip()
    if not prompt or len(prompt) > params.MAX_PROMPT_CHARS:
        raise ValueError(f"user_request must contain between 1 and {params.MAX_PROMPT_CHARS} characters")
    if len(previous_query) > params.MAX_QUERY_CHARS:
        raise ValueError("previous_query exceeds the YCS Query body size limit")

    cached_schema = await query.service.query_schema(backend, request=None)
    schema = {key: value for key, value in cached_schema.items() if key != "cached_at"}
    model_name: str | None = None
    draft: dict[str, Any] | None = None
    constrained_prompt = (
        f"{prompt}\n\nMCP result limit: do not request more than "
        f"{_max_limit(backend)} rows or points."
    )
    async for frame in domains.ycs.query.ai.service.generate_query_stream(
        backend=backend,
        user_prompt=constrained_prompt,
        previous=previous_query,
        request=None,
    ):
        try:
            event = json.loads(frame.get("data", "{}"))
        except (TypeError, ValueError):
            continue
        if event.get("event") == "model":
            model_name = str(event.get("model") or "") or None
        elif event.get("event") == "done":
            draft = event
        elif event.get("event") == "error":
            raise RuntimeError(str(event.get("error") or "YCS Query generation failed"))
    if draft is None:
        raise RuntimeError("The YCS Query agent returned no completed draft")
    candidate = str(draft.get("final") or "")[:params.MAX_QUERY_CHARS]
    valid, error = bool(draft.get("ok")), draft.get("error")
    if valid:
        error = _result_limit_error(backend, candidate)
        valid = error is None
    return schemas.QueryDraft(
        backend=backend,
        query=candidate,
        valid=valid,
        validation_error=(str(error)[:params.MAX_VALIDATION_ERROR_CHARS] if error else None),
        model=model_name,
        attempts=max(1, min(2, int(draft.get("attempts") or 1))),
        schema_fingerprint=_fingerprint(schema),
    )


async def validate_query(backend: str, query_text: str) -> schemas.QueryValidation:
    query_text = query_text.strip()
    if not query_text or len(query_text) > params.MAX_QUERY_CHARS:
        raise ValueError(f"query must contain between 1 and {params.MAX_QUERY_CHARS} characters")
    valid, error, fingerprint = await _validate(backend, query_text)
    return schemas.QueryValidation(
        backend=backend,
        valid=valid,
        validation_error=(str(error)[:params.MAX_VALIDATION_ERROR_CHARS] if error else None),
        schema_fingerprint=fingerprint,
    )


async def run_query(backend: str, query_text: str) -> schemas.QueryRunResult:
    query_text = query_text.strip()
    if not query_text or len(query_text) > params.MAX_QUERY_CHARS:
        raise ValueError(f"query must contain between 1 and {params.MAX_QUERY_CHARS} characters")
    valid, error, _fingerprint_value = await _validate(backend, query_text)
    if not valid:
        return schemas.QueryRunResult(
            backend=backend,
            ok=False,
            error=(str(error or "Query validation failed")[:500]),
        )
    response = await domains.ycs.query.source.service.run_raw_query(
        backend,
        app="ycs",
        body_text=query_text,
        request=None,
    )
    return schemas.QueryRunResult.model_validate(_bound_result(response))


def _bound_result(response: dict[str, Any]) -> dict[str, Any]:
    bounded = {
        "backend": str(response.get("backend") or "")[:64],
        "app": "ycs",
        "ok": bool(response.get("ok")),
        "error": str(response["error"])[:500] if response.get("error") is not None else None,
        "notes": [str(note)[:500] for note in (response.get("notes") or [])[:20]],
        "took_ms": max(0, int(response.get("took_ms") or 0)),
        "total": max(0, response["total"]) if isinstance(response.get("total"), int) else None,
        "hits": [],
        "truncated": False,
    }
    raw_hits = response.get("hits")
    if not isinstance(raw_hits, list):
        raw_hits = []
    bounded["truncated"] = len(raw_hits) > params.MAX_QUERY_RESULT_HITS
    for hit in raw_hits[:params.MAX_QUERY_RESULT_HITS]:
        if not isinstance(hit, dict):
            hit = {"summary": str(hit), "raw": {}}
        safe, truncated = _bound_value(hit, depth=0)
        bounded["truncated"] = bounded["truncated"] or truncated
        bounded["hits"].append(safe)
    if bounded["truncated"]:
        bounded["notes"] = bounded["notes"][:19]
        bounded["notes"].append("Some query output was truncated to keep the MCP response within safe limits.")
    while len(json.dumps(bounded, ensure_ascii=False, default=str).encode("utf-8")) > params.MAX_QUERY_RESULT_BYTES and bounded["hits"]:
        bounded["hits"].pop()
        bounded["truncated"] = True
    if bounded["truncated"] and not any("truncated" in note.lower() for note in bounded["notes"]):
        bounded["notes"].append("Some query output was truncated to keep the MCP response within safe limits.")
    if len(json.dumps(bounded, ensure_ascii=False, default=str).encode("utf-8")) > params.MAX_QUERY_RESULT_BYTES:
        bounded["hits"] = []
        bounded["error"] = bounded["error"] or "Query output exceeded the MCP response limit."
        bounded["notes"] = ["Query output was truncated to keep the MCP response within safe limits."]
        bounded["truncated"] = True
    return bounded


def _bound_value(value: Any, *, depth: int) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) > params.MAX_QUERY_OUTPUT_STRING_CHARS:
            return value[:params.MAX_QUERY_OUTPUT_STRING_CHARS] + "…", True
        return value, False
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if depth >= params.MAX_QUERY_OUTPUT_DEPTH:
        return str(value)[:500], True
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        truncated = len(value) > 100
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                break
            result[str(key)[:160]], was_truncated = _bound_value(item, depth=depth + 1)
            truncated = truncated or was_truncated
        return result, truncated
    if isinstance(value, (list, tuple, set)):
        result = []
        truncated = len(value) > 100
        for index, item in enumerate(value):
            if index >= 100:
                break
            bounded, was_truncated = _bound_value(item, depth=depth + 1)
            result.append(bounded)
            truncated = truncated or was_truncated
        return result, truncated
    if hasattr(value, "model_dump"):
        return _bound_value(value.model_dump(mode="json"), depth=depth + 1)
    if hasattr(value, "value"):
        return _bound_value(value.value, depth=depth + 1)
    rendered = str(value)
    if len(rendered) > params.MAX_QUERY_OUTPUT_STRING_CHARS:
        return rendered[:params.MAX_QUERY_OUTPUT_STRING_CHARS] + "…", True
    return rendered, False
