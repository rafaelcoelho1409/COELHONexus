"""Read-only Qdrant query and schema operations for BD."""
from __future__ import annotations

import logging
import time
from typing import Any

import infra

from ...... import settings
from ..... import rag
from ... import domain, errors, params

logger = logging.getLogger(__name__)


async def run_raw_query(body_text: str) -> dict[str, Any]:
    backend = params.BACKEND_QDRANT
    try:
        parsed = domain.parse_qdrant_body(body_text)
    except errors.QueryNotAllowed as error:
        return domain.raw_response(backend, ok = False, error = str(error))

    started = time.monotonic()
    operation = parsed.operation
    body = dict(parsed.body)
    body.pop("op", None)
    requested_collection = body.pop("collection_name", None)
    query_text = body.pop("query_text", None)
    notes: list[str] = []
    try:
        client = infra.qdrant.service.get_qdrant()
        collection_result = await client.get_collections()
        collection_names = sorted(
            collection.name
            for collection in collection_result.collections
            if collection.name.startswith(params.QDRANT_COLLECTION_PREFIX)
        )[:params.QDRANT_MAX_COLLECTIONS]
    except Exception as error:  # noqa: BLE001 - backend failures belong in the query result envelope.
        logger.warning(f"[bd:query:qdrant] collection lookup failed: {type(error).__name__}: {error}")
        return domain.raw_response(
            backend,
            ok = False,
            error = f"{type(error).__name__}: {str(error)[:300]}",
            took_ms = int((time.monotonic() - started) * 1000),
        )

    if requested_collection:
        if not str(requested_collection).startswith(params.QDRANT_COLLECTION_PREFIX):
            return domain.raw_response(
                backend,
                ok = False,
                error = "The collection must belong to the BD RAG collection family.",
            )
        if requested_collection not in collection_names:
            return domain.raw_response(
                backend,
                ok = False,
                error = f"BD Qdrant collection {requested_collection!r} was not found.",
            )
        collection_names = [requested_collection]
    elif len(collection_names) > 1 and not query_text:
        notes.append(f"Queried {len(collection_names)} BD embedding collections.")

    if query_text:
        try:
            vectors, model = await settings.embeddings.service.embed_texts_async([query_text])
        except Exception as error:  # noqa: BLE001 - report embedding failures through the query result.
            return domain.raw_response(
                backend,
                ok = False,
                error = f"Could not embed the Qdrant search text: {type(error).__name__}: {str(error)[:250]}",
                took_ms = int((time.monotonic() - started) * 1000),
            )
        if not vectors or not vectors[0]:
            return domain.raw_response(
                backend,
                ok = False,
                error = "The configured embedding model returned an empty search vector.",
            )
        expected_collection = rag.source.indexes.qdrant_index.domain.collection_name(model, len(vectors[0]))
        if requested_collection and requested_collection != expected_collection:
            return domain.raw_response(
                backend,
                ok = False,
                error = (
                    "This semantic search uses the configured embedding model and requires collection "
                    f"{expected_collection!r}."
                ),
            )
        if expected_collection not in collection_names:
            try:
                collection_exists = await client.collection_exists(expected_collection)
            except Exception as error:  # noqa: BLE001 - report a collection lookup failure to the user.
                return domain.raw_response(
                    backend,
                    ok = False,
                    error = f"Could not verify the configured BD Qdrant collection: {type(error).__name__}.",
                    took_ms = int((time.monotonic() - started) * 1000),
                )
            if not collection_exists:
                return domain.raw_response(
                    backend,
                    ok = False,
                    error = (
                        "No BD Qdrant collection exists for the configured embedding model. "
                        "Reindex BD RAG before running semantic searches."
                    ),
                )
        collection_names = [expected_collection]
        body["query_vector"] = {
            "name": str(body.pop("using", "dense")),
            "vector": vectors[0],
        }
        operation = "search"

    if not collection_names:
        return domain.raw_response(
            backend,
            notes = ["No BD Qdrant collections are indexed yet."],
            took_ms = int((time.monotonic() - started) * 1000),
            total = 0,
        )

    hits: list[dict[str, Any]] = []
    collection_errors: list[str] = []
    count_total = 0
    for collection_name in collection_names:
        arguments = {**body, "collection_name": collection_name}
        try:
            if operation == "search":
                result = await client.query_points(**domain.translate_search_body(arguments))
                points = getattr(result, "points", result)
            elif operation == "scroll":
                points, _next_page = await client.scroll(**arguments)
            elif operation == "query_points":
                result = await client.query_points(**arguments)
                points = getattr(result, "points", result)
            elif operation == "count":
                result = await client.count(**arguments)
                count_total += int(getattr(result, "count", 0))
                continue
            else:
                return domain.raw_response(backend, ok = False, error = f"Unsupported Qdrant operation: {operation}.")
            hits.extend(domain.qdrant_hit(point) for point in points)
        except Exception as error:  # noqa: BLE001 - keep partial results if one collection fails.
            collection_errors.append(f"{collection_name}: {type(error).__name__}: {str(error)[:140]}")

    if operation == "count":
        return domain.raw_response(
            backend,
            ok = not collection_errors,
            error = "; ".join(collection_errors) if collection_errors else None,
            notes = notes,
            took_ms = int((time.monotonic() - started) * 1000),
            total = count_total,
            hits = [{"summary": f"count={count_total}", "raw": {"count": count_total}}],
        )

    if operation in {"search", "query_points"}:
        hits.sort(
            key = lambda hit: float(hit["raw"].get("score") or 0),
            reverse = True,
        )
    limit = int(body.get("limit", params.QDRANT_DEFAULT_LIMIT))
    hits = hits[:limit]
    if collection_errors:
        notes.extend(collection_errors)
    return domain.raw_response(
        backend,
        ok = bool(hits) or not collection_errors,
        error = "; ".join(collection_errors) if collection_errors and not hits else None,
        notes = notes,
        took_ms = int((time.monotonic() - started) * 1000),
        total = len(hits),
        hits = hits,
    )


async def query_schema() -> dict[str, Any]:
    client = infra.qdrant.service.get_qdrant()
    try:
        result = await client.get_collections()
    except Exception as error:  # noqa: BLE001 - provide a declared schema if Qdrant is unavailable.
        logger.warning(f"[bd:query:schema:qdrant] collection lookup failed: {error}")
        return {"collections": [], "observed_payload_keys": list(params.QDRANT_PAYLOAD_KEYS)}
    collections = []
    for item in result.collections:
        name = item.name
        if not name.startswith(params.QDRANT_COLLECTION_PREFIX):
            continue
        try:
            info = await client.get_collection(name)
            vectors = info.config.params.vectors
            payload_schema = info.payload_schema or {}
            points_count = int(info.points_count or 0)
        except Exception as error:  # noqa: BLE001 - retain the collection if detail lookup fails.
            logger.debug(f"[bd:query:schema:qdrant] collection detail lookup failed for {name}: {error}")
            vectors = {}
            payload_schema = {}
            points_count = 0
        collections.append({
            "name": name,
            "points_count": points_count,
            "vectors_config": domain.json_safe(vectors),
            "payload_schema": domain.json_safe(payload_schema),
            "observed_payload_keys": list(params.QDRANT_PAYLOAD_KEYS),
        })
        if len(collections) >= params.QDRANT_MAX_COLLECTIONS:
            break
    return {"collections": collections}


async def validate_query(
    body_text: str,
    *,
    schema: dict[str, Any],
) -> tuple[bool, str | None]:
    """Validate Qdrant operation, indexed collection, vector name, and filter fields."""
    valid, error = domain.check_query_body(params.BACKEND_QDRANT, body_text)
    if not valid:
        return False, error
    try:
        parsed = domain.parse_qdrant_body(body_text)
    except errors.QueryNotAllowed as error:
        return False, str(error)

    collections = schema.get("collections", [])
    if not isinstance(collections, list) or not collections:
        return False, "No BD Qdrant collection is indexed yet."
    body = parsed.body
    query_text = body.get("query_text")
    if query_text is not None:
        if not isinstance(query_text, str) or not query_text.strip():
            return False, "`query_text` must contain the natural-language search text."
        if len(query_text) > params.MAX_QDRANT_QUERY_TEXT_CHARS:
            return False, f"`query_text` exceeds {params.MAX_QDRANT_QUERY_TEXT_CHARS} characters."
        if body.get("using", "dense") != "dense":
            return False, "Natural-language searches use the indexed `dense` vector."

    requested_collection = body.get("collection_name")
    scoped_collections = [
        collection
        for collection in collections
        if isinstance(collection, dict)
        and (not requested_collection or collection.get("name") == requested_collection)
    ]
    if requested_collection and not scoped_collections:
        return False, f"BD Qdrant collection {requested_collection!r} was not found."
    if query_text and not requested_collection:
        # A text query is embedded with the active indexing model, so it must not
        # be sent to old collections created with different model dimensions.
        scoped_collections = [
            collection
            for collection in scoped_collections
            if isinstance(collection.get("vectors_config"), dict)
            and "dense" in collection["vectors_config"]
        ]
        if not scoped_collections:
            return False, "No BD Qdrant collection has the `dense` vector required for semantic search."

    available_payload_keys = set(params.QDRANT_PAYLOAD_KEYS)
    available_vector_names: set[str] = set()
    for collection in scoped_collections:
        payload_schema = collection.get("payload_schema")
        if isinstance(payload_schema, dict):
            available_payload_keys.update(str(key) for key in payload_schema)
        vectors_config = collection.get("vectors_config")
        if isinstance(vectors_config, dict):
            available_vector_names.update(str(key) for key in vectors_config)
    unknown_filter_keys = sorted(
        key for key in _qdrant_filter_keys(body.get("filter"))
        if key not in available_payload_keys
    )
    if unknown_filter_keys:
        return False, f"Unknown BD Qdrant payload field(s): {', '.join(unknown_filter_keys)}."

    vector_name = body.get("using")
    if query_text:
        vector_name = "dense"
    if vector_name and available_vector_names and vector_name not in available_vector_names:
        return False, f"The selected Qdrant collections do not contain vector {vector_name!r}."
    return True, None


def _qdrant_filter_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = {value["key"]} if isinstance(value.get("key"), str) else set()
        for item in value.values():
            keys.update(_qdrant_filter_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_qdrant_filter_keys(item))
        return keys
    return set()
