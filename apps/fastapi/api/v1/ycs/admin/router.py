"""ycs/admin — ES aggregations, library view, and Celery task-status helpers for FastHTML."""
from __future__ import annotations

from typing import Any

from celery.result import AsyncResult
from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from domains.ycs.content.domain import _absolutize_thumbnail_url
from domains.ycs.graph_builder.params import SOURCE_LABEL
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


@router.get("/pipeline/{extract_id}/stream/{phase}")
async def pipeline_stream_status(
    extract_id: str, phase: str, request: Request,
) -> dict:
    """2026-09-13: aggregator counterpart to `task_status` above, for
    the Videos pipeline's per-video streaming fan-out (Neo4j/Qdrant no
    longer map to one Celery task id — see `pipeline_task.streaming`'s
    docstring). Synthesizes the SAME `{task_id, state, meta, result,
    error}` shape `task_status` returns from Redis counters instead of
    one `AsyncResult`, so `pipeline_panel.js`'s existing bar-rendering
    code (`_setBar`/`_phasePct`/`_phaseLabel`/`_successHint`) needs no
    changes beyond routing the qdrant/neo4j bars to this endpoint
    instead of `/admin/task/{id}`."""
    if phase not in ("neo4j", "qdrant"):
        raise HTTPException(status_code = 400, detail = "phase must be 'neo4j' or 'qdrant'")
    redis = getattr(request.app.state, "redis_aio", None)
    if redis is None:
        raise HTTPException(status_code = 503, detail = "Redis unavailable")
    from domains.ycs.pipeline_task import get_phase_progress
    progress = await get_phase_progress(redis, extract_id, phase)
    payload: dict[str, Any] = {"task_id": extract_id, "state": progress["state"]}
    if progress["state"] == "SUCCESS":
        payload["result"] = progress.get("result") or {}
    else:
        payload["meta"] = progress.get("meta") or {}
    return payload


@router.get("/pipeline/{extract_id}/llm-counters")
async def pipeline_llm_counters(extract_id: str) -> dict:
    """LLM-usage drawer for the Neo4j box — same contract as DD's
    `/planner|synth/debug/graph/{thread_id}/llm-counters`
    (`domains.dd.runtime.service.read_counters`), backed by YCS's
    own counter store (`domains.ycs.runtime.llm_counter`) since YCS's
    extraction isn't a LangGraph graph with a thread_id to key off —
    keyed by extract_id instead, "node" is the video_id being
    extracted rather than a workflow node."""
    from domains.ycs.runtime.llm_counter import read_counters
    return await read_counters(extract_id)


def _status_for(has_transcript: bool, has_neo4j_doc: bool) -> str:
    """3-state status: `done` = all 3 stores present; `partial` = no Neo4j; `failed` = no transcript."""
    if has_transcript and has_neo4j_doc:
        return "done"
    if has_transcript:
        return "partial"
    return "failed"


async def _compute_video_statuses(
    es: AsyncElasticsearch, neo4j_graph: Any | None, video_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Per-video `{status, transcript_langs, transcript_length,
    entity_count}` for `video_ids` — partition-aware (a split video's
    parts carry `video_id="XYZ#p{n}"` + `parent_video_id="XYZ"`;
    presence requires EVERY expected part, not just one).

    2026-09-15: factored out of `list_videos` so `list_videos` (one
    page) and `videos_facets` (whole corpus) compute status through the
    exact same logic — they'd silently drift apart otherwise, which is
    exactly how the facets' "done-only" scoping went stale relative to
    what the listing actually showed."""
    if not video_ids:
        return {}
    transcript_meta: dict[str, dict[str, Any]] = {}
    try:
        t_response = await es.search(
            index = INDEX_TRANSCRIPTIONS,
            size  = min(10000, max(200, len(video_ids) * 10)),
            query = {"bool": {"should": [
                {"terms": {"video_id": video_ids}},
                # `.keyword`, not the bare field — `parent_video_id` is
                # mapped `text` (analyzed) with a `.keyword` sub-field;
                # a `terms` query against the bare name tokenizes and
                # silently matches nothing. This clause has been dead
                # since it was first added (2026-09-14) until fixed
                # live (2026-09-15) — every split video's status was
                # computed as if it had no transcript at all, since its
                # transcript docs (keyed by partition id) were never
                # found when this ran with the PARENT id.
                {"terms": {"parent_video_id.keyword": video_ids}},
            ]}},
            _source = ["video_id", "parent_video_id", "part_total", "lang", "content"],
        )
        grouped: dict[str, list[dict]] = {}
        for h in t_response.get("hits", {}).get("hits", []):
            s = h.get("_source") or {}
            eff_id = s.get("parent_video_id") or s.get("video_id")
            if eff_id:
                grouped.setdefault(eff_id, []).append(s)
        for vid, docs in grouped.items():
            expected = next(
                (d.get("part_total") for d in docs if d.get("part_total")), None,
            ) or 1
            langs = sorted({d.get("lang", "unknown") for d in docs})
            content_len = sum(len(d.get("content") or "") for d in docs)
            transcript_meta[vid] = {
                "has_transcript":    len(docs) >= expected,
                "transcript_langs":  langs,
                "transcript_length": content_len,
            }
    except Exception:
        pass  # best-effort; every video degrades to has_transcript=False

    neo4j_doc_ids: set[str] = set()
    entity_counts: dict[str, int] = {}
    if neo4j_graph is not None:
        try:
            rows = neo4j_graph.query(
                f"MATCH (d:Document:{SOURCE_LABEL}) "
                "WHERE d.video_id IN $vids OR d.parent_video_id IN $vids "
                f"OPTIONAL MATCH (d)-[:MENTIONS]-(e:__Entity__:{SOURCE_LABEL}) "
                "WITH COALESCE(d.parent_video_id, d.video_id) AS vid, "
                "     d.part_total AS part_total, "
                "     count(DISTINCT d) AS n_docs, "
                "     count(DISTINCT e) AS n_entities "
                "RETURN vid, part_total, n_docs, n_entities",
                params = {"vids": video_ids},
            )
            for r in rows:
                vid = r["vid"]
                expected = r.get("part_total") or 1
                if int(r["n_docs"] or 0) >= expected:
                    neo4j_doc_ids.add(vid)
                entity_counts[vid] = entity_counts.get(vid, 0) + int(r["n_entities"] or 0)
        except Exception:
            pass

    out: dict[str, dict[str, Any]] = {}
    for vid in video_ids:
        tmeta = transcript_meta.get(vid, {})
        has_transcript = bool(tmeta.get("has_transcript"))
        out[vid] = {
            "status":            _status_for(has_transcript, vid in neo4j_doc_ids),
            "transcript_langs":  tmeta.get("transcript_langs", []),
            "transcript_length": tmeta.get("transcript_length", 0),
            "entity_count":      entity_counts.get(vid, 0),
        }
    return out


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

    # 2026-09-15: every indexed video is now listed — a video with a
    # transcript but no Neo4j graph yet ("partial") is still fully
    # queryable via Qdrant/RAG, it just hasn't had entity extraction
    # run. Hard-dropping it here (the OLD behavior) meant ANY gap in
    # Neo4j — a wipe, Neo4j simply not having run yet, or a user who
    # never enabled graph extraction for a batch — made those videos
    # vanish from the Library entirely, even with every filter left on
    # "All". `status` is still a real, explicit filter (see below); it
    # was never meant to be an invisible always-on one.
    video_meta = await _compute_video_statuses(
        es, getattr(request.app.state, "neo4j_graph", None), video_ids,
    )

    items: list[dict[str, Any]] = []
    for h in hits:
        src = h["_source"]
        vid = h["_id"]
        m = video_meta.get(vid) or {
            "status": "failed", "transcript_langs": [],
            "transcript_length": 0, "entity_count": 0,
        }
        row_status = m["status"]
        row_langs  = m["transcript_langs"]

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
            "transcript_length": m["transcript_length"],
            "entity_count":      m["entity_count"],
        })

    return {
        "items":        items,
        # `total` = ES cardinality for this query (every status included
        # now); `total_raw` kept identical for backward-compat with any
        # caller still reading it.
        "total":        total_from_es,
        "total_raw":    total_from_es,
        "returned":     len(items),
        "offset":       offset,
        "limit":        limit,
    }


@router.get("/videos/facets")
async def videos_facets(request: Request) -> dict:
    """Facet counts across EVERY indexed video, not just Neo4j-"done"
    ones.

    2026-09-15: previously scoped to done-only video ids — if a video's
    Neo4j processing was behind (or wiped, or never run), its channel
    and language silently vanished from every filter option, and the
    Status filter was permanently stuck offering only "Done" (a fixed
    one-entry list, not a real facet) even though the frontend already
    has CSS for all 3 status pills. Channels/languages now aggregate
    over the whole metadata/transcript indices directly — no id-scoping
    needed since nothing is hidden by status anymore (see
    `list_videos`). Statuses is a REAL 3-way breakdown, computed via the
    same `_compute_video_statuses` `list_videos` uses, so the two can
    never drift apart again."""
    es = _es()
    out: dict[str, list[dict[str, Any]]] = {
        "channels":  [],
        "languages": [],
        "statuses":  [],
    }

    try:
        c_resp = await es.search(
            index = INDEX_METADATA,
            size  = 0,
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

    try:
        # Bounded like `list_videos`'s own transcript lookup — fine at
        # this scale (10k id cap); a corpus past that needs a real
        # scroll, not a bigger constant.
        id_resp = await es.search(
            index = INDEX_METADATA, size = 10000, _source = False,
        )
        all_ids = [h["_id"] for h in id_resp.get("hits", {}).get("hits", [])]
        video_meta = await _compute_video_statuses(
            es, getattr(request.app.state, "neo4j_graph", None), all_ids,
        )
        counts = {"done": 0, "partial": 0, "failed": 0}
        for m in video_meta.values():
            counts[m["status"]] = counts.get(m["status"], 0) + 1
        labels = {"done": "Done", "partial": "Partial", "failed": "Failed"}
        out["statuses"] = [
            {"key": k, "label": labels[k], "count": v}
            for k, v in counts.items() if v > 0
        ]
    except Exception:
        pass

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
