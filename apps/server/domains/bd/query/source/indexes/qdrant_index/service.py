"""Read-only Qdrant query and schema operations for BD."""
from __future__ import annotations
import infra
from ... import domain, errors, params

import logging
import time
from typing import Any


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
    elif len(collection_names) > 1:
        notes.append(f"Queried {len(collection_names)} BD embedding collections.")

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
