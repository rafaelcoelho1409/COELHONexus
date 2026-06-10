"""ycs/admin — ES aggregations + Celery task-status helpers for FastHTML.

Endpoints:
  GET    /admin/ingested-channels      legacy facet (kept for compat)
  GET    /admin/ingested-playlists     legacy facet (kept for compat)
  GET    /admin/task/{task_id}         Celery state passthrough
  GET    /admin/videos                 ⟵ NEW · library rows
  GET    /admin/videos/facets          ⟵ NEW · channel/lang/status counts
  DELETE /admin/videos/{video_id}      ⟵ NEW · single-video wipe
  POST   /admin/videos/bulk-delete     ⟵ NEW · multi-select wipe

The Ingest page's new library view (June 2026 SOTA redesign) calls
`/admin/videos` for the row list, `/admin/videos/facets` for the
filter sidebar counts, and the delete endpoints for hover/bulk
actions. `/admin/ingested-channels` + `/admin/ingested-playlists`
remain for the Ask page's channel-scope multi-select."""
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


# =============================================================================
# Internal helpers
# =============================================================================
def _es() -> AsyncElasticsearch:
    return get_es()


def _absolutize_thumb(url: str | None) -> str:
    """ES `thumbnail_url` → browser-safe absolute URL (or "" when
    absent). Thin wrapper over the content-domain helper so the
    library listing shares the Source preview's exact behavior."""
    return _absolutize_thumbnail_url(url or "")


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


# =============================================================================
# Library view (Ingest page redesign — June 2026 SOTA)
# =============================================================================
def _status_for(has_transcript: bool, has_neo4j_doc: bool) -> str:
    """3-state status pill, derived from cross-store presence:
        - `done`    — metadata + transcript + Neo4j Document
        - `partial` — metadata + transcript only
        - `failed`  — metadata only (transcript fetch failed)
    """
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
    """Library rows for the Ingest page. Joins ES metadata + per-video
    transcript presence + Neo4j Document presence so each row knows
    its own status pill.

    Filtering happens in ES (channel + free-text `q` over title);
    `status` + `lang` are post-filtered in Python because they cross
    multiple stores. limit/offset paginate; the frontend asks for as
    much as it needs (default 50 fits above-the-fold)."""
    es = _es()

    # 1. ES metadata (filtered, paginated). max(0,…) so a UI bug doesn't
    #    send negative offsets straight through.
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

    # "True" library total = distinct video_ids with a Neo4j Document
    # node (= status "done"). "partial" and "failed" rows are hidden
    # from the listing (operational debris), so reporting
    # `total_from_es` or even the transcript-cardinality count would
    # lie about how many rows the user can actually see.
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

    # 2. Transcript presence + dominant language (one terms agg).
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

    # 3. Neo4j Document presence (single Cypher query covers all ids).
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

    # 4. Project + post-filter on status/lang.
    items: list[dict[str, Any]] = []
    for h in hits:
        src = h["_source"]
        vid = h["_id"]
        tmeta = transcript_meta.get(vid, {})
        has_transcript = bool(tmeta.get("has_transcript"))
        has_neo4j_doc  = vid in neo4j_doc_ids
        row_status = _status_for(has_transcript, has_neo4j_doc)
        row_langs  = tmeta.get("transcript_langs", [])

        # ALWAYS drop non-"done" entries — only fully-processed videos
        # (ES metadata + transcript + Neo4j Document) belong in the
        # library listing. "failed" = metadata-only orphans from a
        # Stop-mid-Phase-1 or scrape error; "partial" = transcript
        # but no Neo4j (Phase 3 didn't finish — typically a Stop
        # during Phase 3 or an LLM-cascade exhaustion). Both are
        # operational debris, not queryable units. Cleanup paths:
        # per-row DELETE /admin/videos/{video_id} or Pipeline panel's
        # Wipe button.
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
            # ES stores the field as `thumbnail_url` (yt-dlp's native
            # key), NOT `thumbnail` — asking for the latter returned
            # None and rendered empty rectangles. Mirror the Source
            # preview path (content/service.py), which also reads
            # `thumbnail_url` and absolutizes protocol-relative URLs.
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
        # `total` reflects what the user can actually see — the
        # processed-video count (cardinality over the transcripts
        # index). `total_from_es` is kept on the side for clients
        # that want to flag "N orphan metadata-only entries" (the
        # diff between the two is the count of debris from Stop /
        # mid-fetch failures).
        "total":        n_processed_total,
        "total_raw":    total_from_es,
        "returned":     len(items),
        "offset":       offset,
        "limit":        limit,
    }


@router.get("/videos/facets")
async def videos_facets(request: Request) -> dict:
    """Filter sidebar counts SCOPED TO READY VIDEOS ONLY (status="done"
    = ES metadata + transcript + Neo4j Document all present).

    Before this scoping: Channels showed every channel that had EVER
    been ingested, even if its only videos were partial/failed and thus
    hidden from the listing — clicking the channel chip filtered to 0
    rows, which read as a broken filter. Languages had the same drift
    (aggregated over all transcripts including partial-status ones).

    Pipeline:
      1. Pull the set of done video_ids from Neo4j (`Document.video_id`).
      2. Channels  — terms agg over ES metadata WHERE _id IN done_ids.
      3. Languages — terms agg over ES transcripts WHERE video_id IN done_ids.
      4. Statuses  — single `Done` chip with count = len(done_ids).

    All three facets now share one source of truth — the ready set —
    so a chip with `count = N` always corresponds to exactly N visible
    rows when clicked."""
    es = _es()
    out: dict[str, list[dict[str, Any]]] = {
        "channels":  [],
        "languages": [],
        "statuses":  [],
    }

    # 1. Pull the done set from Neo4j. Empty when Neo4j is down or no
    #    document yet — every facet then renders empty, which is the
    #    honest state (no ready videos → no facets to filter by).
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

    # 2. Channels — agg over ES metadata RESTRICTED to done_ids. ES
    #    `_id` is the video id (set at index time).
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

    # 3. Languages — agg over ES transcripts RESTRICTED to done_ids.
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

    # 4. Statuses — single `Done` chip. `partial` + `failed` chips
    #    were dropped in the prior ship (list_videos hides those rows
    #    unconditionally → filtering for them would yield zero results).
    #    Kept as one read-only chip so the user sees "Done: N" alongside
    #    Channels / Languages.
    out["statuses"] = [
        {"key": "done", "label": "Done", "count": len(done_ids)},
    ]

    return out


@router.delete("/videos/{video_id}")
async def delete_video(video_id: str, request: Request) -> dict:
    """Single-video wipe — drops ES metadata + transcripts, Qdrant
    points, Neo4j Document + Video nodes for one id. Reuses the
    pipeline-side `wipe_videos_data` orchestrator so single-row delete
    and Pipeline-panel `Wipe cache` share one code path."""
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
    """Multi-select wipe. Same one-shot semantics as the single-video
    DELETE — fans out the supplied list to the shared
    `wipe_videos_data` orchestrator. Bulk endpoints are POST (not
    DELETE) because HTTP DELETE doesn't reliably carry a request body."""
    from domains.ycs.pipeline_task import wipe_videos_data
    if not payload.video_ids:
        return {"status": "noop", "summary": {"video_ids": []}}
    summary = await wipe_videos_data(
        video_ids   = payload.video_ids,
        neo4j_graph = getattr(request.app.state, "neo4j_graph", None),
    )
    return {"status": "wiped", "summary": summary}
