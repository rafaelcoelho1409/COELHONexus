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
    *, app: str, q: str, limit: int, offset: int, request: Request,
) -> dict[str, Any]:
    """Multi-index ES search for the selected app.

    YCS path: multi_match across both the metadata index (title +
    description + channel) AND the transcriptions index (content). The
    projector branches on `_index` to render the right hit shape.

    Empty `q` returns a `match_all` page so the user can browse without
    typing — same idiom as the Ingest library view."""
    if not domain.is_supported(app, params.BACKEND_ES):
        return _unsupported(params.BACKEND_ES, app, q)

    es = infra.elasticsearch.service.get_es()
    indexes = f"{infra.elasticsearch.keys.INDEX_METADATA},{infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS}"
    if q.strip():
        query: dict[str, Any] = {
            "multi_match": {
                "query":  q,
                # Title gets the strongest weight (boost 3) so a hit on
                # the video title outranks a coincidental keyword in a
                # 30-minute transcript. `best_fields` picks the highest
                # per-field score (BM25-style) rather than summing.
                "fields": [
                    "title^3", "description", "channel", "content",
                ],
                "type":   "best_fields",
            },
        }
    else:
        query = {"match_all": {}}

    t0 = time.monotonic()
    try:
        with ask.observability.service.es_search_span(
            index = indexes, top_k = limit,
        ):
            response = await es.search(
                index = indexes,
                query = query,
                size  = limit,
                from_ = offset,
                _source = True,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:es] search failed: {type(e).__name__}: {e}")
        return _envelope(
            params.BACKEND_ES, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )

    raw_hits = response.get("hits", {}).get("hits", [])
    total    = int(response.get("hits", {}).get("total", {}).get("value", 0))
    hits     = [domain.project_es_hit(h, app = app) for h in raw_hits]
    return _envelope(params.BACKEND_ES, app, q, hits, total, t0)


async def run_raw_query(
    *, app: str, body_text: str, request: Request | None = None,
) -> dict[str, Any]:
    if not domain.is_supported(app, params.BACKEND_ES):
        return _raw_disallowed(
            params.BACKEND_ES, app,
            f"{app!r} has no presence in Elasticsearch.",
        )
    try:
        parsed = domain.parse_es_body(body_text)
    except errors.QueryNotAllowed as e:
        return _raw_disallowed(params.BACKEND_ES, app, str(e))

    es = infra.elasticsearch.service.get_es()
    indexes = f"{infra.elasticsearch.keys.INDEX_METADATA},{infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS}"
    t0 = time.monotonic()
    notes: list[str] = []
    if parsed.synth_size:
        notes.append(
            f"`size` not supplied — defaulted to {parsed.body['size']}.",
        )

    try:
        with ask.observability.service.es_search_span(
            index = indexes, top_k = parsed.body.get("size", 0), operation = "raw_search",
        ):
            response = await es.search(index = indexes, body = parsed.body)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:raw_es] failed: {type(e).__name__}: {e}")
        return _raw_envelope(
            params.BACKEND_ES, app, t0, ok = False,
            error = f"{type(e).__name__}: {str(e)[:300]}",
            notes = notes,
        )

    raw_hits = response.get("hits", {}).get("hits", [])
    total = int(response.get("hits", {}).get("total", {}).get("value", 0))
    # Project so the right-pane renderer can show a sensible default
    # title, but keep the FULL hit under `raw` so the JSON inspector
    # has everything.
    projected: list[dict[str, Any]] = []
    for h in raw_hits:
        src = h.get("_source", {}) or {}
        title = (
            src.get("title")
            or src.get("video_id")
            or h.get("_id", "")
        )
        projected.append({
            "summary": str(title),
            "raw":     h,
        })
    return _raw_envelope(
        params.BACKEND_ES, app, t0,
        hits  = projected,
        total = total,
        notes = notes,
    )


async def _build_es_schema_live() -> dict[str, Any]:
    """ES live schema — overlay layer for the two-layer merge.

    Per-index payload:
      · mappings    — full ES mapping (field name → type)
      · doc_count   — primary-shard doc count
      · samples     — 2 actual docs (just the _source) so the model sees
                      real values + which fields are populated.
      · field_values — top distinct values per KEYWORD field (terms agg,
                       size=5). Skips text/date/numeric fields where a
                       value sample wouldn't help.

    All fields degrade to empty when the index is empty / unreachable;
    `_build_es_schema` merges this on top of the declared floor so the
    AI prompt always sees the full structural shape."""
    es = infra.elasticsearch.service.get_es()
    indices = [
        infra.elasticsearch.keys.INDEX_METADATA,
        infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS,
    ]
    out: dict[str, Any] = {"indices": {}}
    for idx in indices:
        try:
            with ask.observability.service.es_search_span(
                index = idx, top_k = 0, operation = "get_alias",
            ):
                aliases = await es.indices.get_alias(name = idx)
            if len(aliases) != 1:
                raise RuntimeError(f"expected one physical index behind alias {idx!r}")
            physical_index = next(iter(aliases))
            expected_index = infra.elasticsearch.keys.PHYSICAL_INDEX_BY_ALIAS[idx]
            if physical_index != expected_index:
                raise RuntimeError(
                    f"alias {idx!r} targets {physical_index!r}, expected {expected_index!r}"
                )
            with ask.observability.service.es_search_span(
                index = physical_index, top_k = 0, operation = "get_mapping",
            ):
                mapping = await es.indices.get_mapping(index = physical_index)
                stats   = await es.indices.stats(index = physical_index, metric = "docs")
        except Exception as e:  # noqa: BLE001
            out["indices"][idx] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            continue
        index_stats = stats.get("indices", {}).get(physical_index, {})
        doc_count = index_stats.get("primaries", {}).get("docs", {}).get("count", 0)
        mappings = mapping.get(physical_index, {}).get("mappings", {})
        props    = mappings.get("properties", {}) or {}

        samples: list[dict[str, Any]] = []
        try:
            with ask.observability.service.es_search_span(
                index = idx, top_k = 2, operation = "schema_sample",
            ):
                s_resp = await es.search(
                    index = idx,
                    size  = 2,
                    query = {"match_all": {}},
                )
            for h in s_resp.get("hits", {}).get("hits", []):
                samples.append({
                    "_id":     h.get("_id"),
                    "_source": domain.truncate_doc(h.get("_source", {}) or {}),
                })
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[ycs:query:schema:es] sample fetch failed for {idx}: {e}")

        # Top values for each keyword field (skip if huge / nested).
        field_values: dict[str, list[str]] = {}
        keyword_fields = [
            name for name, cfg in props.items()
            if (cfg.get("type") in ("keyword",))
            and not name.startswith("_")
        ][:12]   # cap so the agg doesn't blow up on indexes with many keywords
        if keyword_fields:
            aggs = {
                f"v_{i}": {"terms": {"field": name, "size": 5}}
                for i, name in enumerate(keyword_fields)
            }
            try:
                with ask.observability.service.es_search_span(
                    index = idx, top_k = 0, operation = "schema_aggs",
                ):
                    a_resp = await es.search(
                        index = idx, size = 0, aggs = aggs,
                    )
                buckets = a_resp.get("aggregations", {}) or {}
                for i, name in enumerate(keyword_fields):
                    raw = buckets.get(f"v_{i}", {}).get("buckets", []) or []
                    vals = [str(b.get("key")) for b in raw if b.get("key") not in (None, "")]
                    if vals:
                        field_values[name] = vals
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ycs:query:schema:es] terms agg failed for {idx}: {e}")

        out["indices"][idx] = {
            "doc_count":    int(doc_count or 0),
            "mappings":     mappings,
            "samples":      samples,
            "field_values": field_values,
        }
    return out


async def _build_es_schema() -> dict[str, Any]:
    """Two-layer ES schema: declared floor + live overlay.

    Declared floor (`domain.declared_es_schema`) is sourced from
    `infra/elasticsearch/schemas.py` so an empty cluster / outage
    still surfaces the full mapping. Live overlay merges per-index
    samples + field_values + observed mappings (in case ES has drifted
    from what we declared) + doc_count."""
    declared = domain.declared_es_schema()
    try:
        live = await _build_es_schema_live()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:schema:es] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return domain.merge_es_schema(declared, live)


async def query_schema() -> dict[str, Any]:
    """Return the declared and live schema for the YCS elasticsearch store."""
    return await _build_es_schema()


async def validate_query(body_text: str) -> tuple[bool, str | None]:
    """Check query shape and let Elasticsearch validate the query clause."""
    valid, error = domain.check_with_safety(body_text, backend = params.BACKEND_ES)
    if not valid:
        return False, error
    try:
        parsed = domain.parse_es_body(body_text)
        client = infra.elasticsearch.service.get_es()
        indexes = (
            f"{infra.elasticsearch.keys.INDEX_METADATA},"
            f"{infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS}"
        )
        result = await client.indices.validate_query(
            index = indexes,
            body = {"query": parsed.body.get("query", {"match_all": {}})},
            explain = True,
        )
    except Exception as error:  # noqa: BLE001 - return details for one repair attempt.
        return False, f"Elasticsearch rejected query validation: {type(error).__name__}: {str(error)[:300]}"
    if result.get("valid", False):
        return True, None
    explanations = result.get("explanations") or []
    details = "; ".join(
        str(item.get("error") or item.get("explanation") or "")
        for item in explanations
        if isinstance(item, dict) and (item.get("error") or item.get("explanation"))
    )
    return False, f"Elasticsearch marked the query invalid{': ' + details if details else '.'}"
