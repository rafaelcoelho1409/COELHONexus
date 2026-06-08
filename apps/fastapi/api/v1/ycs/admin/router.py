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
                "upload_date", "webpage_url", "thumbnail",
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

    # "True" library total = distinct video_ids that have at least one
    # transcript doc. Metadata-only entries (no transcript) are the
    # output of a Stop-mid-Phase-1 or a Playwright scrape failure —
    # they're operational debris, NOT something the user can query.
    # Reporting `total_from_es` would lie: the page would show
    # "5 videos" when the listing only renders 2 done + 1 partial.
    n_processed_total = 0
    try:
        t_count = await es.search(
            index = INDEX_TRANSCRIPTIONS,
            size  = 0,
            aggs  = {"vids": {"cardinality": {"field": "video_id"}}},
        )
        n_processed_total = int(
            t_count.get("aggregations", {}).get("vids", {}).get("value", 0),
        )
    except Exception:
        # If the agg fails, fall back to total_from_es so the count
        # is at least non-zero — the row filter below still hides the
        # failed entries from the listing.
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

        # ALWAYS drop "failed" entries — these are metadata-only rows
        # left behind by a mid-Phase-1 Stop or a transcript-fetch error.
        # They can't be queried (no transcript, no entities), so they
        # don't belong in the library listing. Wipe-by-channel would
        # be the path to clean them up; the per-row delete still works
        # via DELETE /admin/videos/{video_id}.
        if row_status == "failed":
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
            "thumbnail":         src.get("thumbnail"),
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
    """Filter sidebar counts: channels, languages, statuses. One pass
    over ES metadata + transcriptions + Neo4j Documents. Returns the
    sidebar payload as `{channels: [...], languages: [...], statuses:
    [...]}` with `{key, label, count}` entries."""
    es = _es()
    out: dict[str, list[dict[str, Any]]] = {
        "channels":  [],
        "languages": [],
        "statuses":  [],
    }
    # Channels — `_terms_facet` already does what we want.
    try:
        out["channels"] = await _terms_facet(
            es, facet_field = "channel_id", label_field = "channel",
        )
    except Exception:
        pass

    # Languages — agg over transcripts index.
    try:
        t_resp = await es.search(
            index = INDEX_TRANSCRIPTIONS,
            size  = 0,
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

    # Statuses — derived. Count metadata docs that DO and DON'T have a
    # transcript twin; further split done/partial via Neo4j presence.
    try:
        total_meta = (await es.count(index = INDEX_METADATA)).get("count", 0)
        # Distinct video_ids with at least one transcript doc.
        t_resp = await es.search(
            index = INDEX_TRANSCRIPTIONS,
            size  = 0,
            aggs  = {
                "video_ids": {
                    "cardinality": {"field": "video_id"},
                },
            },
        )
        n_with_t = (
            t_resp.get("aggregations", {})
            .get("video_ids", {}).get("value", 0)
        )
        n_failed = max(0, int(total_meta) - int(n_with_t))
        # Documents in Neo4j.
        n_in_neo4j = 0
        g = getattr(request.app.state, "neo4j_graph", None)
        if g is not None:
            try:
                rows = g.query(
                    "MATCH (d:Document) WHERE d.video_id IS NOT NULL "
                    "RETURN count(DISTINCT d.video_id) AS n",
                )
                n_in_neo4j = int(rows[0]["n"]) if rows else 0
            except Exception:
                pass
        n_done    = min(int(n_with_t), int(n_in_neo4j))
        n_partial = max(0, int(n_with_t) - n_done)
        # `failed` chip intentionally dropped — list_videos hides those
        # rows unconditionally, so the filter would yield zero results
        # and read as a UI bug. `n_failed` is still computed above for
        # operational awareness (could surface as a banner) but isn't
        # exposed as a filter facet.
        out["statuses"] = [
            {"key": "done",    "label": "Done",    "count": n_done},
            {"key": "partial", "label": "Partial", "count": n_partial},
        ]
    except Exception:
        pass

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
