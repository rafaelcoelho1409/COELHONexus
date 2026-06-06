"""ycs/admin — ES aggregations + Celery task-status helpers for FastHTML.

Endpoints:
  GET /admin/ingested-channels   → [{channel_id, channel, video_count}, ...]
  GET /admin/ingested-playlists  → [{playlist_id, playlist_title, video_count}, ...]
  GET /admin/task/{task_id}      → {state, meta, result?}

`ingested-channels` / `ingested-playlists` drive the Ingest page's
"library" view; `task/{task_id}` is polled by the Ingest page after
the Source page dispatches an `extract_*` task."""
from __future__ import annotations

from typing import Any

from celery.result import AsyncResult
from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, HTTPException

from infra.celery import app as celery_app
from infra.elasticsearch import INDEX_METADATA, get_es


router = APIRouter()


# =============================================================================
# Internal helpers
# =============================================================================
def _es() -> AsyncElasticsearch:
    return get_es()


async def _terms_facet(
    es:           AsyncElasticsearch,
    facet_field:  str,
    label_field:  str,
    extra_field:  str | None = None,
    size:         int        = 1000,
) -> list[dict[str, Any]]:
    """Generic ES terms aggregation over `INDEX_METADATA` grouped by
    `facet_field`, with a `top_hits` sub-agg projecting the
    human-readable `label_field` (+ optional `extra_field`) from the
    first matching doc per bucket."""
    sources = [facet_field, label_field]
    if extra_field:
        sources.append(extra_field)
    aggs = {
        "facets": {
            "terms": {"field": facet_field, "size": size},
            "aggs": {
                "first_doc": {
                    "top_hits": {"size": 1, "_source": sources},
                },
            },
        },
    }
    try:
        response = await es.search(
            index = INDEX_METADATA,
            size  = 0,
            aggs  = aggs,
        )
    except Exception as e:
        raise HTTPException(
            status_code = 503,
            detail      = f"Elasticsearch query failed: {e}",
        )
    buckets = (
        response.get("aggregations", {})
        .get("facets", {})
        .get("buckets", [])
    )
    out: list[dict[str, Any]] = []
    for bucket in buckets:
        key = bucket.get("key")
        if not key:
            continue
        hits = (
            bucket.get("first_doc", {})
            .get("hits", {})
            .get("hits", [])
        )
        source = hits[0].get("_source", {}) if hits else {}
        item: dict[str, Any] = {
            facet_field: key,
            label_field: source.get(label_field) or key,
            "video_count": bucket.get("doc_count", 0),
        }
        if extra_field:
            item[extra_field] = source.get(extra_field)
        out.append(item)
    # Stable sort: highest video_count first → secondary by label
    out.sort(
        key = lambda x: (-(x.get("video_count") or 0), x.get(label_field) or ""),
    )
    return out


# =============================================================================
# Endpoints
# =============================================================================
@router.get("/ingested-channels")
async def ingested_channels() -> dict:
    """Distinct channels in the metadata index with per-channel video count.
    Backs the Ask page's "scope to channel(s)" multi-select."""
    es = _es()
    items = await _terms_facet(
        es,
        facet_field = "channel_id",
        label_field = "channel",
    )
    return {"total": len(items), "items": items}


@router.get("/ingested-playlists")
async def ingested_playlists() -> dict:
    """Distinct playlists in the metadata index with per-playlist video
    count. Used on the Ingest page library view."""
    es = _es()
    items = await _terms_facet(
        es,
        facet_field = "playlist_id",
        label_field = "playlist_title",
        extra_field = "channel_id",
    )
    return {"total": len(items), "items": items}


@router.get("/task/{task_id}")
async def task_status(task_id: str) -> dict:
    """Wrap Celery `AsyncResult` so the FastHTML polling loop has one
    JSON shape to consume. `meta` is the dict the task posts via
    `self.update_state(meta=...)`; `result` is the task return value
    (only populated on SUCCESS)."""
    if not task_id:
        raise HTTPException(status_code = 400, detail = "task_id required")
    try:
        async_result = AsyncResult(task_id, app = celery_app)
        state = async_result.state
        info: Any = async_result.info
    except Exception as e:
        raise HTTPException(
            status_code = 503,
            detail      = f"Celery result backend error: {e}",
        )
    payload: dict[str, Any] = {
        "task_id": task_id,
        "state":   state,
    }
    if state == "PROGRESS" and isinstance(info, dict):
        payload["meta"] = info
    elif state == "FAILURE":
        # Celery serializes Exception → repr in `info` when failed.
        payload["error"] = str(info) if info is not None else "Unknown error"
    elif state == "SUCCESS":
        payload["result"] = info
    elif isinstance(info, dict):
        payload["meta"] = info
    return payload
