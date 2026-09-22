"""admin service — ES/Neo4j listing orchestration shared by endpoints."""
from __future__ import annotations

import domains, infra
from . import domain

from elasticsearch import AsyncElasticsearch
from fastapi import HTTPException
from typing import Any


def _es() -> AsyncElasticsearch:
    return infra.elasticsearch.service.get_es()


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
            index = infra.elasticsearch.keys.INDEX_METADATA,
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
            index = infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS,
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
                f"MATCH (d:Document:{domains.ycs.graph_builder.params.SOURCE_LABEL}) "
                "WHERE d.video_id IN $vids OR d.parent_video_id IN $vids "
                f"OPTIONAL MATCH (d)-[:MENTIONS]-(e:__Entity__:{domains.ycs.graph_builder.params.SOURCE_LABEL}) "
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
            "status":            domain._status_for(has_transcript, vid in neo4j_doc_ids),
            "transcript_langs":  tmeta.get("transcript_langs", []),
            "transcript_length": tmeta.get("transcript_length", 0),
            "entity_count":      entity_counts.get(vid, 0),
        }
    return out
