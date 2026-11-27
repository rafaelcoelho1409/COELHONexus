"""Pure query response and read-safety helpers for BD retrieval stores."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import infra

from . import entities, errors, params, patterns


def raw_response(
    backend: str,
    *,
    ok: bool = True,
    error: str | None = None,
    notes: list[str] | None = None,
    took_ms: int = 0,
    total: int | None = None,
    hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "app": params.APP_BD,
        "ok": ok,
        "error": error,
        "notes": list(notes or []),
        "took_ms": took_ms,
        "total": total,
        "hits": list(hits or []),
    }


def schema_fingerprint(schema: dict[str, Any]) -> str:
    """Return a stable identifier for a backend schema snapshot."""
    encoded = json.dumps(
        schema,
        ensure_ascii = False,
        sort_keys = True,
        separators = (",", ":"),
        default = str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def bound_raw_response(response: dict[str, Any]) -> dict[str, Any]:
    """Bound query output size while preserving the HTTP response contract."""
    bounded = dict(response)
    bounded["backend"] = str(bounded.get("backend") or "")[:64]
    bounded["app"] = params.APP_BD
    bounded["ok"] = bool(bounded.get("ok"))
    bounded["error"] = (
        str(bounded["error"])[:500]
        if bounded.get("error") is not None
        else None
    )
    try:
        bounded["took_ms"] = max(0, int(bounded.get("took_ms") or 0))
    except (TypeError, ValueError):
        bounded["took_ms"] = 0
    total = bounded.get("total")
    bounded["total"] = max(0, total) if isinstance(total, int) else None
    source_hits = bounded.get("hits")
    if not isinstance(source_hits, list):
        source_hits = []
    truncated = len(source_hits) > params.MAX_QUERY_RESULT_HITS
    safe_hits = []
    for hit in source_hits[:params.MAX_QUERY_RESULT_HITS]:
        safe_hit, hit_truncated = _bound_json_value(hit)
        truncated = truncated or hit_truncated
        safe_hits.append(safe_hit)
    bounded["hits"] = safe_hits

    notes = bounded.get("notes")
    if not isinstance(notes, list):
        notes = []
    bounded["notes"] = [str(note)[:500] for note in notes[:20]]
    if truncated:
        bounded["notes"].append(
            "Some query output was truncated to keep the response within safe limits."
        )

    while _serialized_size(bounded) > params.MAX_QUERY_RESULT_BYTES and bounded["hits"]:
        bounded["hits"].pop()
        truncated = True
    if truncated and not any("truncated" in note.lower() for note in bounded["notes"]):
        bounded["notes"].append(
            "Some query output was truncated to keep the response within safe limits."
        )

    if _serialized_size(bounded) > params.MAX_QUERY_RESULT_BYTES:
        bounded = {
            "backend": bounded["backend"],
            "app": params.APP_BD,
            "ok": bounded["ok"],
            "error": str(bounded.get("error") or "Query output exceeded the response limit.")[:500],
            "notes": ["Query output was truncated to keep the response within safe limits."],
            "took_ms": bounded["took_ms"],
            "total": bounded["total"],
            "hits": [],
        }
    return bounded


def _bound_json_value(
    value: Any,
    *,
    depth: int = 0,
    remaining_nodes: list[int] | None = None,
) -> tuple[Any, bool]:
    """Limit deeply nested or exceptionally large values from indexed data."""
    if remaining_nodes is None:
        remaining_nodes = [params.MAX_QUERY_OUTPUT_NODES_PER_HIT]
    if remaining_nodes[0] <= 0:
        return "[truncated]", True
    remaining_nodes[0] -= 1
    if isinstance(value, str):
        if len(value) > params.MAX_QUERY_OUTPUT_STRING_CHARS:
            return value[:params.MAX_QUERY_OUTPUT_STRING_CHARS] + "…", True
        return value, False
    if depth >= params.MAX_QUERY_OUTPUT_DEPTH:
        return str(value)[:500], True
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        truncated = len(value) > 100
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                break
            bounded_item, item_truncated = _bound_json_value(
                item,
                depth = depth + 1,
                remaining_nodes = remaining_nodes,
            )
            bounded[str(key)[:160]] = bounded_item
            truncated = truncated or item_truncated
            if remaining_nodes[0] <= 0:
                truncated = True
                break
        return bounded, truncated
    if isinstance(value, (list, tuple, set)):
        bounded_items = []
        truncated = len(value) > 100
        for index, item in enumerate(value):
            if index >= 100:
                break
            bounded_item, item_truncated = _bound_json_value(
                item,
                depth = depth + 1,
                remaining_nodes = remaining_nodes,
            )
            bounded_items.append(bounded_item)
            truncated = truncated or item_truncated
            if remaining_nodes[0] <= 0:
                truncated = True
                break
        return bounded_items, truncated
    if hasattr(value, "model_dump"):
        return _bound_json_value(
            value.model_dump(mode = "json"),
            depth = depth + 1,
            remaining_nodes = remaining_nodes,
        )
    if hasattr(value, "dict"):
        return _bound_json_value(
            value.dict(),
            depth = depth + 1,
            remaining_nodes = remaining_nodes,
        )
    if hasattr(value, "value"):
        return _bound_json_value(
            value.value,
            depth = depth + 1,
            remaining_nodes = remaining_nodes,
        )
    safe_value = json_safe(value)
    if isinstance(safe_value, str) and len(safe_value) > params.MAX_QUERY_OUTPUT_STRING_CHARS:
        return safe_value[:params.MAX_QUERY_OUTPUT_STRING_CHARS] + "…", True
    return safe_value, False


def _serialized_size(value: dict[str, Any]) -> int:
    return len(
        json.dumps(value, ensure_ascii = False, separators = (",", ":"), default = str)
        .encode("utf-8")
    )


def check_query_body(backend: str, body: str) -> tuple[bool, str | None]:
    """Apply BD's read-only query guards and Qdrant collection scope check."""
    if len(body) > params.MAX_QUERY_BODY_CHARS:
        return False, f"Query body exceeds {params.MAX_QUERY_BODY_CHARS} characters."
    try:
        if backend == params.BACKEND_ELASTICSEARCH:
            parse_elasticsearch_body(body)
        elif backend == params.BACKEND_QDRANT:
            parsed = parse_qdrant_body(body)
            collection = parsed.body.get("collection_name")
            if collection and not str(collection).startswith(params.QDRANT_COLLECTION_PREFIX):
                return False, "The collection must belong to the BD RAG collection family."
        elif backend == params.BACKEND_NEO4J:
            assert_cypher_readonly(body)
        else:
            return False, f"Unsupported backend: {backend}."
    except errors.QueryNotAllowed as error:
        return False, str(error)
    return True, None


def normalize_neo4j_query(body: str) -> str:
    """Unwrap the common single-field JSON response into plain Cypher."""
    try:
        candidate = json.loads(body)
    except json.JSONDecodeError:
        return body
    if (
        isinstance(candidate, dict)
        and set(candidate) == {"query"}
        and isinstance(candidate["query"], str)
    ):
        return candidate["query"].strip()
    return body


def assert_cypher_readonly(statement: str) -> None:
    """Reject Cypher write clauses and mutation procedures."""
    if not statement.strip():
        raise errors.QueryNotAllowed("Empty Cypher.")

    scan = patterns.CYPHER_STRING.sub(" ", statement).lower()
    scan = patterns.CYPHER_LINE_COMMENT.sub(" ", scan)
    scan = patterns.CYPHER_BLOCK_COMMENT.sub(" ", scan)
    scan = scan.strip()
    if not patterns.CYPHER_READ_QUERY_START.search(scan):
        raise errors.QueryNotAllowed(
            "Cypher must start with a read-only MATCH, WITH, UNWIND, RETURN, "
            "or approved schema-inspection clause.",
        )
    limit_values = patterns.CYPHER_LIMIT_VALUE.findall(scan)
    if not limit_values:
        raise errors.QueryNotAllowed("Neo4j queries must include an explicit LIMIT.")
    if any(int(value) > params.NEO4J_MAX_LIMIT for value in limit_values):
        raise errors.QueryNotAllowed(
            f"Neo4j `LIMIT` must be <= {params.NEO4J_MAX_LIMIT}.",
        )

    tokens = patterns.CYPHER_IDENTIFIER.findall(scan)
    bad = sorted({token for token in tokens if token in params.CYPHER_WRITE_KEYWORDS})
    if bad:
        raise errors.QueryNotAllowed(
            f"Cypher contains write keyword(s): {', '.join(bad)}. "
            "The Query page is read-only — use MATCH / RETURN only.",
        )

    procedure = patterns.CYPHER_WRITE_PROCEDURE.search(scan)
    if procedure:
        raise errors.QueryNotAllowed(
            f"Cypher calls a write/mutation procedure ({procedure.group(0).strip()}). "
            "Only read procedures (db.labels(), db.schema.*, apoc.meta.*) "
            "are allowed.",
        )


def parse_elasticsearch_body(text: str) -> entities.ParsedElasticsearchBody:
    """Parse an Elasticsearch search body and enforce its safe result cap."""
    if not text.strip():
        raise errors.QueryNotAllowed("Empty Elasticsearch body.")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as error:
        raise errors.QueryNotAllowed(
            f"Invalid JSON: {error.msg} at line {error.lineno} col {error.colno}",
        ) from error
    if not isinstance(body, dict):
        raise errors.QueryNotAllowed(
            "Elasticsearch body must be a JSON object (got "
            f"{type(body).__name__}).",
        )
    for forbidden in ("script", "scripted_metric"):
        if forbidden in body:
            raise errors.QueryNotAllowed(f"`{forbidden}` clauses are blocked (write surface).")

    synth_size = False
    if "size" not in body:
        body["size"] = params.ES_DEFAULT_SIZE
        synth_size = True
    else:
        try:
            size = int(body["size"])
        except (TypeError, ValueError) as error:
            raise errors.QueryNotAllowed(f"`size` must be an integer, got {body['size']!r}.") from error
        if size < 0:
            raise errors.QueryNotAllowed("`size` must be >= 0.")
        if size > params.ES_MAX_SIZE:
            raise errors.QueryNotAllowed(f"`size` must be <= {params.ES_MAX_SIZE}.")

    return entities.ParsedElasticsearchBody(body = body, synth_size = synth_size)


def parse_qdrant_body(text: str) -> entities.ParsedQdrantOperation:
    """Parse the JSON operation envelope and allow read-only Qdrant calls."""
    if not text.strip():
        raise errors.QueryNotAllowed("Empty Qdrant body.")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as error:
        raise errors.QueryNotAllowed(
            f"Invalid JSON: {error.msg} at line {error.lineno} col {error.colno}",
        ) from error
    if not isinstance(body, dict):
        raise errors.QueryNotAllowed(
            f"Qdrant body must be a JSON object (got {type(body).__name__}).",
        )

    operation = body.get("op", "search")
    if operation not in params.QDRANT_READ_OPERATIONS:
        raise errors.QueryNotAllowed(
            f"Qdrant op {operation!r} is not allowed. "
            f"Read-only ops: {', '.join(params.QDRANT_READ_OPERATIONS)}.",
        )
    if "query_text" in body:
        query_text = body["query_text"]
        if operation not in {"search", "query_points"}:
            raise errors.QueryNotAllowed("`query_text` is only supported by Qdrant search operations.")
        if not isinstance(query_text, str) or not query_text.strip():
            raise errors.QueryNotAllowed("`query_text` must contain natural-language search text.")
        if len(query_text) > params.MAX_QDRANT_QUERY_TEXT_CHARS:
            raise errors.QueryNotAllowed(
                f"`query_text` exceeds {params.MAX_QDRANT_QUERY_TEXT_CHARS} characters.",
            )
        if "query_vector" in body or "query" in body:
            raise errors.QueryNotAllowed("Use `query_text` or a vector query, not both.")
    if operation in {"search", "scroll", "query_points"}:
        limit = body.get("limit", params.QDRANT_DEFAULT_LIMIT)
        try:
            limit = int(limit)
        except (TypeError, ValueError) as error:
            raise errors.QueryNotAllowed(f"`limit` must be an integer, got {limit!r}.") from error
        if limit < 1:
            raise errors.QueryNotAllowed("`limit` must be >= 1.")
        if limit > params.QDRANT_MAX_LIMIT:
            raise errors.QueryNotAllowed(f"`limit` must be <= {params.QDRANT_MAX_LIMIT}.")
        body["limit"] = limit

    return entities.ParsedQdrantOperation(operation = operation, body = body)


def translate_search_body(body: dict[str, Any]) -> dict[str, Any]:
    """Translate legacy Qdrant `search` arguments to `query_points` arguments."""
    translated = dict(body)
    query_vector = translated.pop("query_vector", None)
    query, using = query_vector, None
    if isinstance(query_vector, dict) and "name" in query_vector and "vector" in query_vector:
        using, query = query_vector["name"], query_vector["vector"]
    elif (
        isinstance(query_vector, list)
        and len(query_vector) == 2
        and isinstance(query_vector[0], str)
        and isinstance(query_vector[1], list)
    ):
        using, query = query_vector
    translated["query"] = query
    if using is not None:
        translated["using"] = using
    return translated


def _neo4j_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, infra.neo4j.service.Node):
        return {
            "_kind": "node",
            "id": value.element_id,
            "labels": list(value.labels),
            "properties": {key: _neo4j_value(item) for key, item in dict(value).items()},
        }
    if isinstance(value, infra.neo4j.service.Relationship):
        return {
            "_kind": "relationship",
            "id": value.element_id,
            "type": value.type,
            "start": value.start_node.element_id if value.start_node else None,
            "end": value.end_node.element_id if value.end_node else None,
            "properties": {key: _neo4j_value(item) for key, item in dict(value).items()},
        }
    if isinstance(value, infra.neo4j.service.Path):
        return {
            "_kind": "path",
            "nodes": [_neo4j_value(node) for node in value.nodes],
            "rels": [_neo4j_value(relationship) for relationship in value.relationships],
        }
    if isinstance(value, (list, tuple)):
        return [_neo4j_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _neo4j_value(item) for key, item in value.items()}
    return str(value)


def neo4j_jsonify(row: dict[str, Any]) -> dict[str, Any]:
    """Convert Neo4j driver values into nested JSON-compatible structures."""
    return {key: _neo4j_value(value) for key, value in row.items()}


def json_safe(value: Any) -> Any:
    """Convert Qdrant model values into the JSON-compatible API shape."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(mode = "json"))
    if hasattr(value, "dict"):
        return json_safe(value.dict())
    if hasattr(value, "value"):
        return json_safe(value.value)
    return str(value)


def qdrant_hit(point: Any) -> dict[str, Any]:
    if isinstance(point, dict):
        point_id = point.get("id", "")
        score = point.get("score")
        payload = point.get("payload") or {}
    else:
        point_id = getattr(point, "id", "")
        score = getattr(point, "score", None)
        payload = getattr(point, "payload", None) or {}
    summary = payload.get("content") or payload.get("book_id") or str(point_id)
    return {
        "summary": str(summary)[:240],
        "raw": {
            "id": str(point_id),
            "score": score,
            "payload": payload,
        },
    }
