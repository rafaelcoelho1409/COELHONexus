"""ycs/admin — ES aggregations, library view, and Celery task-status helpers for FastHTML."""
from __future__ import annotations

from typing import Any

from celery.result import AsyncResult
from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from domains.ycs.content.domain import _absolutize_thumbnail_url
from infra.celery import app as celery_app
from infra.elasticsearch import (
    INDEX_METADATA,
    INDEX_TRANSCRIPTIONS,
    get_es,
)


router = APIRouter()


def _es() -> AsyncElasticsearch:
    return get_es()


def _absolutize_thumb(url: str | None) -> str:
    """Thin wrapper over content-domain helper so library listing shares Source preview's exact behavior."""
    return _absolutize_thumbnail_url(url or "")


async def _terms_facet(
    es:           AsyncElasticsearch,
    facet_field:  str,
    label_field:  str,
    extra_field:  str | None = None,
    size:         int        = 1000,
) -> list[dict[str, Any]]:
    """ES terms aggregation over `INDEX_METADATA` by `facet_field` with a `top_hits` label lookup."""
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
    out.sort(
        key = lambda x: (-(x.get("video_count") or 0), x.get(label_field) or ""),
    )
    return out


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
    """Celery `AsyncResult` passthrough. `meta` = `self.update_state(meta=...)` payload; `result` = SUCCESS return value."""
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


def _status_for(has_transcript: bool, has_neo4j_doc: bool) -> str:
    """3-state status: `done` = all 3 stores present; `partial` = no Neo4j; `failed` = no transcript."""
    if has_transcript and has_neo4j_doc:
        return "done"
    if has_transcript:
        return "partial"
    return "failed"


@router.get("/videos")
async def list_videos(
    request: Request,
    q:       str | None = None,
    channel: str | None = None,
    status:  str | None = None,
    lang:    str | None = None,
    limit:   int        = 50,
    offset:  int        = 0,
) -> dict:
    """Library rows — joins ES metadata, transcript presence, and Neo4j Document presence per video.
    Channel/`q` filters run in ES; `status`+`lang` are post-filtered in Python (cross-store)."""
    es = _es()

    # max(0,…) guards against UI bugs sending negative offsets.
    must: list[dict[str, Any]] = []
    if channel:
        must.append({"term": {"channel_id": channel}})
    if q:
        must.append({
            "multi_match": {
                "query":  q,
                "fields": ["title^3", "description", "channel"],
                "type":   "best_fields",
            },
        })
    query: dict[str, Any] = {"bool": {"must": must}} if must else {"match_all": {}}
    try:
        response = await es.search(
            index = INDEX_METADATA,
            query = query,
            size  = max(1, min(int(limit), 500)),
            from_ = max(0, int(offset)),
            sort  = [{"_score": "desc"}, {"upload_date": "desc"}],
            _source = [
                "id", "title", "channel", "channel_id", "duration",
                "duration_string", "view_count", "like_count",
                "upload_date", "webpage_url", "thumbnail_url",
                "playlist_id", "playlist_title", "description",
            ],
        )
    except Exception as e:
        raise HTTPException(
            status_code = 503, detail = f"Elasticsearch error: {e}",
        )
    hits = response.get("hits", {}).get("hits", [])
    total_from_es = response.get("hits", {}).get("total", {}).get("value", 0)
    video_ids = [h["_id"] for h in hits]

    # Only "done" rows appear in the listing; total_from_es includes debris ("partial"/"failed"), so it overstates what the user can see.
    n_processed_total = 0
    g = getattr(request.app.state, "neo4j_graph", None)
    if g is not None:
        try:
            rows = g.query(
                "MATCH (d:Document) WHERE d.video_id IS NOT NULL "
                "RETURN count(DISTINCT d.video_id) AS n",
            )
            n_processed_total = int(rows[0]["n"]) if rows else 0
        except Exception:
            # Neo4j hiccup → fall back to total_from_es so the count
            # is non-zero. The row filter still hides non-done rows.
            n_processed_total = total_from_es
    else:
        n_processed_total = total_from_es

    transcript_meta: dict[str, dict[str, Any]] = {}
    if video_ids:
        try:
            t_response = await es.search(
                index = INDEX_TRANSCRIPTIONS,
                size  = 0,
                query = {"terms": {"video_id": video_ids}},
                aggs  = {
                    "per_video": {
                        "terms": {"field": "video_id", "size": len(video_ids)},
                        "aggs": {
                            "langs": {"terms": {"field": "lang", "size": 5}},
                            "any_doc": {
                                "top_hits": {
                                    "size": 1,
                                    "_source": ["content"],
                                },
                            },
                        },
                    },
                },
            )
            buckets = (
                t_response.get("aggregations", {})
                .get("per_video", {}).get("buckets", [])
            )
            for b in buckets:
                vid = b["key"]
                lang_buckets = b.get("langs", {}).get("buckets", [])
                langs = [lb["key"] for lb in lang_buckets] or ["unknown"]
                top = b.get("any_doc", {}).get("hits", {}).get("hits", [])
                content_len = (
                    len(top[0].get("_source", {}).get("content", ""))
                    if top else 0
                )
                transcript_meta[vid] = {
                    "has_transcript":    True,
                    "transcript_langs":  langs,
                    "transcript_length": content_len,
                }
        except Exception:
            # Best-effort; UI degrades to "transcript=False" everywhere.
            pass

    neo4j_doc_ids: set[str] = set()
    entity_counts: dict[str, int] = {}
    g = getattr(request.app.state, "neo4j_graph", None)
    if g is not None and video_ids:
        try:
            rows = g.query(
                "MATCH (d:Document) "
                "WHERE d.video_id IN $vids "
                "OPTIONAL MATCH (d)-[:MENTIONS]-(e:__Entity__) "
                "WITH d.video_id AS vid, count(DISTINCT e) AS n_entities "
                "RETURN vid, n_entities",
                params = {"vids": video_ids},
            )
            for r in rows:
                vid = r["vid"]
                neo4j_doc_ids.add(vid)
                entity_counts[vid] = int(r["n_entities"] or 0)
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    for h in hits:
        src = h["_source"]
        vid = h["_id"]
        tmeta = transcript_meta.get(vid, {})
        has_transcript = bool(tmeta.get("has_transcript"))
        has_neo4j_doc  = vid in neo4j_doc_ids
        row_status = _status_for(has_transcript, has_neo4j_doc)
        row_langs  = tmeta.get("transcript_langs", [])

        # Drop non-"done": "failed" = metadata-only orphan; "partial" = no Neo4j (Phase 3 incomplete). Both are debris, not queryable.
        if row_status != "done":
            continue
        if status and row_status != status:
            continue
        if lang and lang not in row_langs:
            continue

        items.append({
            "video_id":          vid,
            "title":             src.get("title"),
            "channel":           src.get("channel"),
            "channel_id":        src.get("channel_id"),
            "duration":          src.get("duration"),
            "duration_string":   src.get("duration_string"),
            "view_count":        src.get("view_count"),
            "like_count":        src.get("like_count"),
            "upload_date":       src.get("upload_date"),
            "webpage_url":       src.get("webpage_url"),
            # yt-dlp stores `thumbnail_url`, not `thumbnail` — the wrong key returns None and renders empty rectangles.
            "thumbnail":         _absolutize_thumb(src.get("thumbnail_url")),
            "playlist_id":       src.get("playlist_id"),
            "playlist_title":    src.get("playlist_title"),
            "status":            row_status,
            "transcript_langs":  row_langs,
            "transcript_length": tmeta.get("transcript_length", 0),
            "entity_count":      entity_counts.get(vid, 0),
        })

    return {
        "items":        items,
        # `total` = visible processed videos; `total_raw` = ES cardinality (includes debris); diff = orphan count.
        "total":        n_processed_total,
        "total_raw":    total_from_es,
        "returned":     len(items),
        "offset":       offset,
        "limit":        limit,
    }


@router.get("/videos/facets")
async def videos_facets(request: Request) -> dict:
    """Facet counts scoped to done-only videos so every chip corresponds to exactly N visible rows.
    Without scoping, channels with only partial/failed videos appear as filter chips that yield 0 rows."""
    es = _es()
    out: dict[str, list[dict[str, Any]]] = {
        "channels":  [],
        "languages": [],
        "statuses":  [],
    }

    # Empty done_ids → empty facets, which is honest (no ready videos → nothing to filter by).
    done_ids: list[str] = []
    g = getattr(request.app.state, "neo4j_graph", None)
    if g is not None:
        try:
            rows = g.query(
                "MATCH (d:Document) WHERE d.video_id IS NOT NULL "
                "RETURN collect(DISTINCT d.video_id) AS ids",
            )
            if rows and rows[0].get("ids"):
                done_ids = [str(x) for x in rows[0]["ids"] if x]
        except Exception:
            pass

    if not done_ids:
        # No ready videos → empty facets + zero Done chip. Skip the
        # ES aggs to avoid issuing a `terms:{values:[]}` filter (some
        # ES versions error on empty terms).
        out["statuses"] = [{"key": "done", "label": "Done", "count": 0}]
        return out

    # ES `_id` is the video_id (set at index time).
    try:
        c_resp = await es.search(
            index = INDEX_METADATA,
            size  = 0,
            query = {"ids": {"values": done_ids}},
            aggs  = {
                "by_channel": {
                    "terms": {"field": "channel_id", "size": 1000},
                    "aggs": {
                        "first_doc": {
                            "top_hits": {
                                "size": 1, "_source": ["channel"],
                            },
                        },
                    },
                },
            },
        )
        buckets = (
            c_resp.get("aggregations", {})
            .get("by_channel", {}).get("buckets", [])
        )
        for b in buckets:
            key = b.get("key")
            if not key:
                continue
            hits = b.get("first_doc", {}).get("hits", {}).get("hits", [])
            label = (
                hits[0].get("_source", {}).get("channel")
                if hits else None
            ) or key
            out["channels"].append({
                "channel_id":  key,
                "channel":     label,
                "video_count": int(b.get("doc_count", 0)),
            })
        out["channels"].sort(
            key = lambda x: (-int(x.get("video_count") or 0), x.get("channel") or ""),
        )
    except Exception:
        pass

    try:
        t_resp = await es.search(
            index = INDEX_TRANSCRIPTIONS,
            size  = 0,
            query = {"terms": {"video_id": done_ids}},
            aggs  = {
                "by_lang": {"terms": {"field": "lang", "size": 50}},
            },
        )
        for b in t_resp.get("aggregations", {}).get("by_lang", {}).get("buckets", []):
            out["languages"].append({
                "key":   b["key"],
                "label": b["key"],
                "count": int(b.get("doc_count", 0)),
            })
    except Exception:
        pass

    # `partial`/`failed` chips omitted — list_videos hides those rows, so filtering for them yields nothing.
    out["statuses"] = [
        {"key": "done", "label": "Done", "count": len(done_ids)},
    ]

    return out


@router.delete("/videos/{video_id}")
async def delete_video(video_id: str, request: Request) -> dict:
    """Drop ES metadata + transcripts, Qdrant points, and Neo4j nodes for one video."""
    from domains.ycs.pipeline_task import wipe_videos_data
    if not video_id:
        raise HTTPException(status_code = 400, detail = "video_id required")
    summary = await wipe_videos_data(
        video_ids   = [video_id],
        neo4j_graph = getattr(request.app.state, "neo4j_graph", None),
    )
    return {"status": "wiped", "summary": summary}


class BulkDeleteRequest(BaseModel):
    video_ids: list[str]


@router.post("/videos/bulk-delete")
async def bulk_delete_videos(
    payload: BulkDeleteRequest, request: Request,
) -> dict:
    """Multi-select wipe. POST (not DELETE) because HTTP DELETE doesn't reliably carry a body."""
    from domains.ycs.pipeline_task import wipe_videos_data
    if not payload.video_ids:
        return {"status": "noop", "summary": {"video_ids": []}}
    summary = await wipe_videos_data(
        video_ids   = payload.video_ids,
        neo4j_graph = getattr(request.app.state, "neo4j_graph", None),
    )
    return {"status": "wiped", "summary": summary}
