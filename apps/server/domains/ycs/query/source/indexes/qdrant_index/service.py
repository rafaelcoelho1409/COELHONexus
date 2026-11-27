"""YCS query operations for this retrieval backend."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request

import infra

from ..... import ask
from ... import domain, errors, params

logger = logging.getLogger(__name__)


def _query_error_message(error: Exception, *, collection: str) -> str:
    """Turn a missing YCS collection into an actionable Query error."""
    message = str(error).replace("\\'", "'")
    if f"Collection `{collection}` doesn't exist" in message:
        return (
            f"The YCS Qdrant collection `{collection}` does not exist. "
            "Run YCS Qdrant ingestion/indexing before querying."
        )
    return f"{type(error).__name__}: {message[:200]}"


def _unsupported(backend: str, app: str, q: str) -> dict[str, Any]:
    return domain.unsupported_response(backend, app, q)


def _envelope(
    backend: str,
    app: str,
    q: str,
    hits: list[dict[str, Any]],
    total: int,
    t0: float,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "app": app,
        "supported": True,
        "namespace": domain.namespace_label(app, backend),
        "q": q,
        "total": total,
        "took_ms": int((time.monotonic() - t0) * 1000),
        "hits": hits,
        "error": error,
    }


def _raw_envelope(
    backend: str,
    app: str,
    t0: float,
    *,
    hits: list[dict[str, Any]] | None = None,
    total: int | None = None,
    notes: list[str] | None = None,
    error: str | None = None,
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "app": app,
        "ok": ok,
        "error": error,
        "notes": list(notes or []),
        "took_ms": int((time.monotonic() - t0) * 1000),
        "total": total,
        "hits": list(hits or []),
    }


def _raw_disallowed(backend: str, app: str, message: str) -> dict[str, Any]:
    return domain.raw_disallowed_response(backend, app, message)


async def query_simple(
    *, app: str, q: str, limit: int, request: Request,
) -> dict[str, Any]:
    """Qdrant kNN over the app's collection.

    With a non-empty query we embed it via the dense embedder shared
    with ingestion (`request.app.state.smart_retriever.qdrant_retriever
    .dense_embeddings`) — same NIM model used at write time, so cosine
    is well-calibrated. With an empty query we fall back to `scroll`
    (= browse) so the user can sample what's stored without typing.

    YCS path searches a hybrid (dense+sparse) collection; we ONLY query
    the dense vector here. Keeping one path = one mental model. For YCS
    hybrid retrieval the agentic RAG pipeline still owns that surface
    (`/agents/search`)."""
    if not domain.is_supported(app, params.BACKEND_QDRANT):
        return _unsupported(params.BACKEND_QDRANT, app, q)

    collection = params.APP_BACKENDS[app][params.BACKEND_QDRANT].target

    client = infra.qdrant.service.get_qdrant()
    t0 = time.monotonic()
    raw_q = q.strip()

    if raw_q:
        smart = getattr(request.app.state, "smart_retriever", None)
        embedder = getattr(getattr(smart, "qdrant_retriever", None), "dense_embeddings", None)
        if embedder is None:
            return _envelope(
                params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = "dense embedder not initialized (YCS lifespan failed?)",
            )
        try:
            vector = embedder.embed_query(raw_q)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ycs:query:qdrant] embed failed: {type(e).__name__}: {e}")
            return _envelope(
                params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = f"embed failed: {type(e).__name__}: {str(e)[:160]}",
            )
        # YCS uses NAMED vectors ("dense"/"sparse").
        using = "dense" if app == params.APP_YCS else None
        try:
            # 2026-09-17: `AsyncQdrantClient.search()` was removed in
            # qdrant-client 1.16 (live-confirmed: `AttributeError:
            # 'AsyncQdrantClient' object has no attribute 'search'` —
            # this call site had silently been dead since whatever
            # upgrade dropped it). `query_points()` is the replacement:
            # the named-vector selector moves from a `(name, vector)`
            # tuple into its own `using=` kwarg, and the vector itself
            # goes bare into `query=`. Same shape `retriever/service.py`'s
            # `QdrantHybridRetriever` already uses for the RAG path.
            with ask.observability.service.qdrant_search_span(
                collection = collection, top_k = limit,
            ):
                response = await client.query_points(
                    collection_name = collection,
                    query           = vector,
                    using           = using,
                    limit           = limit,
                    with_payload    = True,
                )
                results = response.points
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ycs:query:qdrant] search failed: {type(e).__name__}: {e}")
            return _envelope(
                params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = _query_error_message(e, collection = collection),
            )
        hits = [domain.project_qdrant_point(p, app = app) for p in results]
        return _envelope(params.BACKEND_QDRANT, app, q, hits, total = len(hits), t0 = t0)

    try:
        with ask.observability.service.qdrant_search_span(
            collection = collection, top_k = limit, operation = "scroll",
        ):
            records, _next = await client.scroll(
                collection_name = collection,
                limit           = limit,
                with_payload    = True,
                with_vectors    = False,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:qdrant] scroll failed: {type(e).__name__}: {e}")
        return _envelope(
            params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
            error = _query_error_message(e, collection = collection),
        )
    hits = [domain.project_qdrant_point(r, app = app) for r in records]
    return _envelope(params.BACKEND_QDRANT, app, q, hits, total = len(hits), t0 = t0)


async def run_raw_query(
    *, app: str, body_text: str, request: Request | None = None,
) -> dict[str, Any]:
    if not domain.is_supported(app, params.BACKEND_QDRANT):
        return _raw_disallowed(
            params.BACKEND_QDRANT, app,
            f"{app!r} has no presence in Qdrant.",
        )
    try:
        parsed = domain.parse_qdrant_body(body_text)
    except errors.QueryNotAllowed as e:
        return _raw_disallowed(params.BACKEND_QDRANT, app, str(e))

    collection = params.APP_BACKENDS[app][params.BACKEND_QDRANT].target

    client = infra.qdrant.service.get_qdrant()
    t0 = time.monotonic()
    notes: list[str] = []
    op   = parsed.op
    body = dict(parsed.body)
    body.pop("op", None)
    # Pin the collection — refuse a user-supplied override silently.
    if "collection_name" in body and body["collection_name"] != collection:
        notes.append(
            f"`collection_name` override ignored; pinned to {collection!r}.",
        )
    body["collection_name"] = collection

    try:
        with ask.observability.service.qdrant_search_span(
            collection = collection, top_k = 0, operation = f"raw_{op}",
        ):
            if op == "search":
                # 2026-09-17: `AsyncQdrantClient.search()` was removed in
                # qdrant-client 1.16 (live-confirmed via the semantic-search
                # tab throwing `AttributeError: 'AsyncQdrantClient' object
                # has no attribute 'search'`) — "search" is this editor's
                # DEFAULT op (`domain.py::parse_qdrant_body`), so this was
                # broken for anyone who didn't explicitly type `"op":
                # "query_points"`. Translate the legacy body shape and
                # dispatch through `query_points()` instead, same as the
                # explicit `query_points` branch below.
                r = await client.query_points(**domain.translate_search_body(body))
                results = getattr(r, "points", r)
            elif op == "scroll":
                records, _ = await client.scroll(**body)
                results    = records
            elif op == "query_points":
                r = await client.query_points(**body)
                results = getattr(r, "points", r)
            elif op == "count":
                n = await client.count(**body)
                return _raw_envelope(
                    params.BACKEND_QDRANT, app, t0,
                    hits  = [{"summary": f"count={n.count}", "raw": {"count": n.count}}],
                    total = int(n.count),
                    notes = notes,
                )
            else:
                return _raw_disallowed(params.BACKEND_QDRANT, app, f"unknown op {op!r}")
    except TypeError as e:
        # Pydantic / qdrant-client kwargs mismatch on user-supplied body.
        return _raw_envelope(
            params.BACKEND_QDRANT, app, t0, ok = False,
            error = f"Invalid Qdrant body for op={op!r}: {e}",
            notes = notes,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:raw_qdrant] failed: {type(e).__name__}: {e}")
        return _raw_envelope(
            params.BACKEND_QDRANT, app, t0, ok = False,
            error = _query_error_message(e, collection = collection),
            notes = notes,
        )

    projected: list[dict[str, Any]] = []
    for p in results:
        payload = (getattr(p, "payload", None) or {})
        pid     = str(getattr(p, "id", ""))
        score   = getattr(p, "score", None)
        title   = (
            payload.get("title")
            or payload.get("video_id")
            or pid
        )
        projected.append({
            "summary": str(title),
            "raw": {
                "id":      pid,
                "score":   score,
                "payload": payload,
            },
        })
    return _raw_envelope(
        params.BACKEND_QDRANT, app, t0,
        hits  = projected,
        total = len(projected),
        notes = notes,
    )


async def _build_qdrant_schema_live() -> dict[str, Any]:
    """Qdrant live schema — overlay for the two-layer merge.

    Returns the per-collection vectors_config + declared payload_schema
    + observed_payload_keys (union of keys across sampled payloads) +
    3 sample payloads. Degrades to empty when the collection is empty;
    `_build_qdrant_schema` merges this on top of the declared floor."""
    collection = params.APP_BACKENDS[params.APP_YCS][params.BACKEND_QDRANT].target
    client = infra.qdrant.service.get_qdrant()
    try:
        with ask.observability.service.qdrant_search_span(
            collection = collection, top_k = 0, operation = "get_collection",
        ):
            info = await client.get_collection(collection_name = collection)
    except Exception as e:  # noqa: BLE001
        return {"collections": [{
            "name": collection,
            "error": f"{type(e).__name__}: {str(e)[:160]}",
        }]}

    def _vec_summary(v):
        if v is None: return None
        if hasattr(v, "size"):
            return {"size": v.size, "distance": str(getattr(v, "distance", None))}
        if isinstance(v, dict):
            return {
                name: {"size": cfg.size, "distance": str(getattr(cfg, "distance", None))}
                for name, cfg in v.items()
            }
        return str(v)

    # Sample payloads: payload_schema only has indexed keys; scrolling catches additional unindexed ones.
    samples: list[dict[str, Any]] = []
    try:
        with ask.observability.service.qdrant_search_span(
            collection = collection, top_k = 3, operation = "schema_scroll",
        ):
            records, _ = await client.scroll(
                collection_name = collection,
                limit           = 3,
                with_payload    = True,
                with_vectors    = False,
            )
        for r in records:
            payload = (getattr(r, "payload", None) or {})
            samples.append({
                "id":      str(getattr(r, "id", "")),
                "payload": domain.truncate_doc(payload),
            })
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[ycs:query:schema:qdrant] sample scroll failed: {e}")

    observed_keys: set[str] = set()
    for s in samples:
        observed_keys.update((s.get("payload") or {}).keys())

    return {
        "collections": [{
            "name":           collection,
            "points_count":   int(getattr(info, "points_count", 0) or 0),
            "vectors_config": _vec_summary(getattr(getattr(info, "config", None), "params", None).vectors  # type: ignore[union-attr]
                if getattr(info, "config", None) else None),
            "payload_schema": {
                k: {"data_type": getattr(v, "data_type", str(v))}
                for k, v in (getattr(info, "payload_schema", None) or {}).items()
            },
            "observed_payload_keys": sorted(observed_keys),
            "samples":               samples,
        }],
    }


async def _build_qdrant_schema() -> dict[str, Any]:
    """Two-layer Qdrant schema: declared floor + live overlay.

    Declared floor (`domain.declared_qdrant_schema`) lists the FULL
    canonical payload keys from `ingestion.domain
    .build_payload`. Live overlay adds the actual points_count + real
    sample payloads (so the LLM sees concrete values, not just keys)."""
    declared = domain.declared_qdrant_schema()
    try:
        live = await _build_qdrant_schema_live()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:schema:qdrant] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return domain.merge_qdrant_schema(declared, live)


async def query_schema() -> dict[str, Any]:
    """Return the declared and live schema for the YCS qdrant store."""
    return await _build_qdrant_schema()


async def validate_query(
    body_text: str,
    *,
    schema: dict[str, Any],
) -> tuple[bool, str | None]:
    """Check the read operation, collection scope, payload keys, and vector name."""
    valid, error = domain.check_with_safety(body_text, backend = params.BACKEND_QDRANT)
    if not valid:
        return False, error
    try:
        parsed = domain.parse_qdrant_body(body_text)
    except errors.QueryNotAllowed as error:
        return False, str(error)

    collections = schema.get("collections", [])
    if not isinstance(collections, list) or not collections:
        return False, "The YCS Qdrant collection is not available."
    requested = parsed.body.get("collection_name")
    configured_name = params.APP_BACKENDS[params.APP_YCS][params.BACKEND_QDRANT].target
    if requested and requested != configured_name:
        return False, f"YCS queries are scoped to collection {configured_name!r}."
    matching = [
        collection
        for collection in collections
        if isinstance(collection, dict) and collection.get("name") == configured_name
    ]
    if not matching:
        return False, f"The YCS Qdrant collection {configured_name!r} is not present in the live schema."

    available_keys = set(params.QDRANT_EXPECTED_PAYLOAD_KEYS)
    available_vectors: set[str] = set()
    for collection in matching:
        payload_schema = collection.get("payload_schema")
        if isinstance(payload_schema, dict):
            available_keys.update(str(key) for key in payload_schema)
        vectors_config = collection.get("vectors_config")
        if isinstance(vectors_config, dict):
            available_vectors.update(str(key) for key in vectors_config)
    filter_body = parsed.body.get("filter") or parsed.body.get("scroll_filter")
    unknown_keys = sorted(key for key in _filter_keys(filter_body) if key not in available_keys)
    if unknown_keys:
        return False, f"Unknown YCS Qdrant payload field(s): {', '.join(unknown_keys)}."
    vector_name = parsed.body.get("using")
    if vector_name and available_vectors and vector_name not in available_vectors:
        return False, f"The YCS Qdrant collection does not contain vector {vector_name!r}."
    return True, None


def _filter_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {value["key"]} if isinstance(value.get("key"), str) else set()
        for item in value.values():
            found.update(_filter_keys(item))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_filter_keys(item))
        return found
    return set()
