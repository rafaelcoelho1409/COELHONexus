"""ycs/query — pure projection helpers (Functional Core).

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no logging. Same
inputs → same outputs. The projectors here take raw store responses
(ES `_source` dicts, Qdrant points, Neo4j records) and produce
uniform `QueryHit` dicts so the imperative shell in `service.py`
stays a thin orchestrator."""
from __future__ import annotations

from typing import Any

from .params import (
    APP_RR,
    APP_YCS,
    BACKEND_ES,
    BACKEND_NEO4J,
    BACKEND_QDRANT,
    SNIPPET_CHARS,
)


def _snippet(text: str | None) -> str:
    """Cap free-text fields so a single hit can't bloat the response."""
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= SNIPPET_CHARS:
        return text
    return text[:SNIPPET_CHARS].rstrip() + "…"


# Elasticsearch — YCS only (metadata + transcriptions). The two indexes
# have different shapes so we route by the `_index` ES echoes back on
# every hit.
_ES_METADATA_INDEX:        str = "coelhonexus-youtube-metadata"
_ES_TRANSCRIPTIONS_INDEX:  str = "coelhonexus-youtube-transcriptions"


def project_es_hit(hit: dict[str, Any], app: str = APP_YCS) -> dict[str, Any]:
    """ES `{_index, _id, _score, _source}` → `QueryHit` dict.

    Two-index branching: the metadata index carries the human-friendly
    `title` + `webpage_url`; the transcriptions index carries the
    `content` + a `video_id` foreign key. Title falls back to the
    video_id so transcript hits don't render with an empty title."""
    src   = hit.get("_source", {}) or {}
    index = hit.get("_index", "")
    hit_id = str(hit.get("_id", ""))
    score = hit.get("_score")

    if index == _ES_TRANSCRIPTIONS_INDEX:
        video_id = src.get("video_id") or hit_id.split("_")[0]
        title    = f"Transcript · {video_id} ({src.get('lang') or 'n/a'})"
        snippet  = _snippet(src.get("content"))
        url      = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    else:
        # Metadata index (or any future index that follows its shape).
        title   = src.get("title") or hit_id
        snippet = _snippet(src.get("description"))
        url     = src.get("webpage_url") or ""

    return {
        "kind":    BACKEND_ES,
        "app":     app,
        "id":      hit_id,
        "title":   title,
        "snippet": snippet,
        "score":   float(score) if isinstance(score, (int, float)) else None,
        "url":     url,
        "extra":   {
            "index":   index,
            "_source": src,
        },
    }


# Qdrant — YCS (`youtube-transcripts`) and RR (`radar_papers`). Both
# collections embed via the same NIM model (2048d cosine) so query-side
# embedding is a shared path; only payload shape differs.
def project_qdrant_point(point: Any, app: str) -> dict[str, Any]:
    """Qdrant point (ScoredPoint or Record) → `QueryHit` dict.

    YCS payload: `content / video_id / title / channel / webpage_url`.
    RR  payload: `arxiv_id / title / authors / categories / signal`."""
    payload = (getattr(point, "payload", None) or {}) if not isinstance(point, dict) else point.get("payload", {})
    pid     = str(getattr(point, "id", "") if not isinstance(point, dict) else point.get("id", ""))
    score   = getattr(point, "score", None) if not isinstance(point, dict) else point.get("score")

    if app == APP_RR:
        arxiv_id = payload.get("arxiv_id") or pid
        title    = payload.get("title") or arxiv_id
        snippet  = _snippet(payload.get("abstract") or payload.get("content"))
        url      = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    else:
        # YCS — youtube-transcripts collection
        video_id = payload.get("video_id") or ""
        chunk    = payload.get("chunk_index")
        title    = payload.get("title") or video_id
        if chunk is not None and video_id:
            title = f"{title}  · chunk {chunk}"
        snippet  = _snippet(payload.get("content"))
        url      = payload.get("webpage_url") or (
            f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        )

    return {
        "kind":    BACKEND_QDRANT,
        "app":     app,
        "id":      pid,
        "title":   title,
        "snippet": snippet,
        "score":   float(score) if isinstance(score, (int, float)) else None,
        "url":     url,
        "extra":   {"payload": payload},
    }


# Neo4j — YCS (Document/Video/Channel/__Entity__) and RR (Paper/Author/
# Concept/Source). The Cypher in `service.py` returns a uniform projection
# dict; the helper below just re-shapes it into a QueryHit.
def project_neo4j_row(row: dict[str, Any], app: str) -> dict[str, Any]:
    """Cypher row → `QueryHit`.

    Expected row shape (built by service-side Cypher):
      `{label, key, title, snippet, url, properties}`."""
    label      = row.get("label") or ""
    key        = str(row.get("key") or "")
    title      = row.get("title") or key
    snippet    = _snippet(row.get("snippet"))
    url        = row.get("url") or ""
    properties = row.get("properties") or {}

    return {
        "kind":    BACKEND_NEO4J,
        "app":     app,
        "id":      f"{label}:{key}" if label else key,
        "title":   title,
        "snippet": snippet,
        "score":   None,
        "url":     url,
        "extra":   {
            "label":      label,
            "properties": properties,
        },
    }
