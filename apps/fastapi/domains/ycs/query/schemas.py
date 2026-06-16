"""ycs/query — request + response Pydantic schemas (boundary validation).

Per docs/CODE-CONVENTIONS.md §2 + §7: Pydantic stays at the HTTP
boundary; internal value-shapes (`QueryHit` is one of these) are plain
dataclasses living in `entities.py`. But since these schemas are
*returned* over JSON, Pydantic's `model_dump()` keeps the shape under
one validation regime + supports OpenAPI doc generation."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .params import APPS, BACKENDS, DEFAULT_LIMIT, MAX_LIMIT


AppLiteral     = Literal["dd", "ycs", "rr"]
BackendLiteral = Literal["elasticsearch", "qdrant", "neo4j"]


class QueryRequest(BaseModel):
    """Free-text query, scoped to one app. The backend is implicit in
    the route path (`POST /query/elasticsearch` vs `.../qdrant` vs
    `.../neo4j`) — keeps the router slim and the request body single-
    purpose."""
    app:   AppLiteral = "ycs"
    q:     str        = Field(default = "", description = "Free-text query. Empty = browse-all.")
    limit: int        = Field(default = DEFAULT_LIMIT, ge = 1, le = MAX_LIMIT)
    # Pagination is only meaningful for ES + Neo4j (Qdrant kNN is unranked
    # beyond `limit`). The Qdrant endpoint ignores this.
    offset: int = Field(default = 0, ge = 0)


class QueryHit(BaseModel):
    """Uniform projection across all three backends so the frontend's
    result renderer is one switch on `kind`, not three.

    Field meanings per backend:
      ES     — `id`=doc id, `title`=metadata title, `snippet`=content
               excerpt, `score`=BM25, `extra`=full _source.
      Qdrant — `id`=point id, `title`=payload.title, `snippet`=content
               or abstract, `score`=cosine similarity, `extra`=payload.
      Neo4j  — `id`=node id/key, `title`=display name (title/name),
               `snippet`=description/text, `score`=None,
               `extra`={labels, properties, related}."""
    kind:    str               = Field(description = "Backend identifier — one of {elasticsearch, qdrant, neo4j}")
    app:     str
    id:      str
    title:   str               = ""
    snippet: str               = ""
    score:   float | None      = None
    url:     str               = ""
    extra:   dict[str, Any]    = Field(default_factory = dict)


class QueryResponse(BaseModel):
    """`supported=False` short-circuits the UI to a "no data in this
    backend for this app" empty state, separately from "supported but
    zero hits". `namespace` doubles as the UI caption ("Searching in:
    YCS · metadata + transcripts")."""
    backend:    BackendLiteral
    app:        AppLiteral
    supported:  bool
    namespace:  str            = ""
    q:          str            = ""
    total:      int            = 0
    took_ms:    int            = 0
    hits:       list[QueryHit] = Field(default_factory = list)
    # Surfaced when the store call raised but we want to keep the page
    # functional (greys out the result list, shows the error chip
    # without a 5xx).
    error:      str | None     = None


class NamespaceEntry(BaseModel):
    available: bool
    label:     str = ""
    target:    str = ""


class NamespaceMap(BaseModel):
    """Returned by `GET /query/namespaces`. The UI fetches this on
    first load to know which `(app, backend)` chips to grey out."""
    apps:     list[str]                              = Field(default_factory = lambda: list(APPS))
    backends: list[str]                              = Field(default_factory = lambda: list(BACKENDS))
    matrix:   dict[str, dict[str, NamespaceEntry]]   = Field(default_factory = dict)


# ---------------------------------------------------------------------- #
# Raw query — Phase 1 of the SOTA workbench (`docs` thread, 2026-06-15)
# ---------------------------------------------------------------------- #
class RawQueryRequest(BaseModel):
    """Raw DSL/Cypher/JSON typed by the user in the CodeMirror editor.

    `app` is currently pinned to `"ycs"` by the frontend but threads
    through the same matrix so re-enabling the cross-app pivot is one
    JS line + a router-side payload.

    The `body` is the editor's text content verbatim:
      - Elasticsearch → JSON request-body for `_search`
      - Qdrant        → JSON `{"op": ..., ...}` (see safety.parse_qdrant_body)
      - Neo4j         → Cypher source"""
    app:  AppLiteral = "ycs"
    body: str        = Field(default = "", description = "Editor content.")


class RawQueryHit(BaseModel):
    """Renderer-friendly projection of one row, generic across backends.

    Each backend's `service.raw_*` writes the FULL store-side record
    here under `raw`; `summary` is a short label so the right-pane
    renderers don't have to know each backend's payload shape just
    to print a fallback title."""
    summary: str            = ""
    raw:     dict[str, Any] = Field(default_factory = dict)


class RawQueryResponse(BaseModel):
    """`ok=False` short-circuits the UI to the error band; `notes` is a
    list of user-visible advisories (e.g. "default size=20 synthesized
    because the request body omitted it")."""
    backend:    BackendLiteral
    app:        AppLiteral
    ok:         bool
    error:      str | None       = None
    notes:      list[str]        = Field(default_factory = list)
    took_ms:    int              = 0
    # ES: total + hits_raw; Qdrant: points_raw; Neo4j: records_raw.
    # The renderer chooses one based on `backend`.
    total:      int | None       = None
    hits:       list[RawQueryHit] = Field(default_factory = list)


# ---------------------------------------------------------------------- #
# Schema discovery — Phase 3
# ---------------------------------------------------------------------- #
class SchemaResponse(BaseModel):
    """Per-backend schema snapshot. Shape is intentionally loose (a
    dict) — the renderer's job is to read the keys it knows about, the
    store's actual schema is what we care about.

    `cached_at` is the unix-epoch second the snapshot was taken; the UI
    can render "Refreshed Ns ago" without computing it.

    Typical shape:
      ES → `{"indices": {name: {"mappings": {...}, "doc_count": N}, ...}}`
      Qdrant → `{"collections": [{"name": ..., "vectors_count": ..., "payload_schema": ...}, ...]}`
      Neo4j → `{"labels": [...], "relationship_types": [...], "node_properties": {label: [props]}}`"""
    backend:    BackendLiteral
    app:        AppLiteral
    cached_at:  int
    schema_:    dict[str, Any] = Field(default_factory = dict, alias = "schema")

    class Config:
        populate_by_name = True


# ---------------------------------------------------------------------- #
# AI text-to-DSL — Phase 4
# ---------------------------------------------------------------------- #
class AIGenerateRequest(BaseModel):
    """`prompt` is the user's natural-language description; `previous`
    (optional) is whatever's currently in the editor so the model can
    do `"add a filter for channel X"` follow-ups instead of starting
    from scratch."""
    app:      AppLiteral = "ycs"
    prompt:   str        = ""
    previous: str        = ""


# ---------------------------------------------------------------------- #
# Query history — Phase 5
# ---------------------------------------------------------------------- #
class HistoryEntry(BaseModel):
    id:         int
    backend:    BackendLiteral
    app:        AppLiteral
    body:       str
    prompt:     str       = ""
    created_at: str       = ""
    favorite:   bool      = False


class HistoryList(BaseModel):
    items: list[HistoryEntry] = Field(default_factory = list)
    total: int                = 0


class HistorySaveRequest(BaseModel):
    backend:  BackendLiteral
    app:      AppLiteral = "ycs"
    body:     str
    prompt:   str         = ""
    favorite: bool        = False
