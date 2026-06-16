"""ycs/query — imperative shell. ES + Qdrant + Neo4j search per app.

Per docs/CODE-CONVENTIONS.md §4: I/O orchestration only. Pure projection
delegated to `domain.py`; identifier strings + the (app, backend) map
delegated to `params.py`; HTTP boundary validation to `schemas.py`.

All three entry points (`query_es`, `query_qdrant`, `query_neo4j`)
share the same shape:
    in:  app, q, limit, offset, request
    out: QueryResponse (`supported=False` short-circuit if the app
         has no presence in that backend).

The functions are best-effort: a store hiccup degrades to
`error=...` on the response (HTTP 200 with the error field set)
rather than 5xx, so the Query page can render one failed tab without
killing the other two."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import Request

from infra.elasticsearch import (
    INDEX_METADATA,
    INDEX_TRANSCRIPTIONS,
    get_es,
)
from infra.neo4j import get_driver
from infra.neo4j.params import NEO4J_DATABASE
from infra.qdrant import get_qdrant

from . import domain
from .params import (
    APP_RR,
    APP_YCS,
    BACKEND_ES,
    BACKEND_NEO4J,
    BACKEND_QDRANT,
    is_supported,
    namespace_label,
)
from .safety import (
    QueryNotAllowed,
    assert_cypher_readonly,
    parse_es_body,
    parse_qdrant_body,
)
from .schemas import (
    QueryHit,
    QueryResponse,
    RawQueryHit,
    RawQueryResponse,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# Result envelope helpers
# ---------------------------------------------------------------------- #
def _unsupported(backend: str, app: str, q: str) -> QueryResponse:
    """Same shape as a real response — `supported=False` is the only
    signal the UI needs to render the "no data here" state."""
    return QueryResponse(
        backend   = backend,           # type: ignore[arg-type]
        app       = app,               # type: ignore[arg-type]
        supported = False,
        namespace = "",
        q         = q,
        hits      = [],
    )


def _envelope(
    backend: str, app: str, q: str, hits: list[dict[str, Any]],
    total: int, t0: float, error: str | None = None,
) -> QueryResponse:
    return QueryResponse(
        backend   = backend,           # type: ignore[arg-type]
        app       = app,               # type: ignore[arg-type]
        supported = True,
        namespace = namespace_label(app, backend),
        q         = q,
        total     = total,
        took_ms   = int((time.monotonic() - t0) * 1000),
        hits      = [QueryHit(**h) for h in hits],
        error     = error,
    )


# ====================================================================== #
# Elasticsearch
# ====================================================================== #
async def query_es(
    *, app: str, q: str, limit: int, offset: int, request: Request,
) -> QueryResponse:
    """Multi-index ES search for the selected app.

    YCS path: multi_match across both the metadata index (title +
    description + channel) AND the transcriptions index (content). The
    projector branches on `_index` to render the right hit shape.

    Empty `q` returns a `match_all` page so the user can browse without
    typing — same idiom as the Ingest library view."""
    if not is_supported(app, BACKEND_ES):
        return _unsupported(BACKEND_ES, app, q)

    es: AsyncElasticsearch = get_es()
    indexes = f"{INDEX_METADATA},{INDEX_TRANSCRIPTIONS}"
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
        response = await es.search(
            index = indexes,
            query = query,
            size  = limit,
            from_ = offset,
            _source = True,
        )
    except Exception as e:
        logger.warning(f"[ycs:query:es] search failed: {type(e).__name__}: {e}")
        return _envelope(
            BACKEND_ES, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )

    raw_hits = response.get("hits", {}).get("hits", [])
    total    = int(response.get("hits", {}).get("total", {}).get("value", 0))
    hits     = [domain.project_es_hit(h, app = app) for h in raw_hits]
    return _envelope(BACKEND_ES, app, q, hits, total, t0)


# ====================================================================== #
# Qdrant
# ====================================================================== #
async def query_qdrant(
    *, app: str, q: str, limit: int, request: Request,
) -> QueryResponse:
    """Qdrant kNN over the app's collection.

    With a non-empty query we embed it via the dense embedder shared
    with ingestion (`request.app.state.smart_retriever.qdrant_retriever
    .dense_embeddings`) — same NIM model used at write time, so cosine
    is well-calibrated. With an empty query we fall back to `scroll`
    (= browse) so the user can sample what's stored without typing.

    YCS path searches a hybrid (dense+sparse) collection; we ONLY query
    the dense vector here because RR's collection is dense-only. Keeping
    one path = one mental model. For YCS hybrid retrieval the agentic
    RAG pipeline still owns that surface (`/agents/search`)."""
    if not is_supported(app, BACKEND_QDRANT):
        return _unsupported(BACKEND_QDRANT, app, q)

    # Collection name comes from `params.AppNamespace.target` so adding
    # a new (app, qdrant_collection) pair is a one-line change there.
    from .params import APP_BACKENDS
    collection = APP_BACKENDS[app][BACKEND_QDRANT].target

    client = get_qdrant()
    t0 = time.monotonic()
    raw_q = q.strip()

    if raw_q:
        # Embed via the YCS dense embedder (lazy lookup; same model
        # used by both YCS ingestion and RR ingestion → cosine is
        # well-calibrated across both collections).
        smart = getattr(request.app.state, "smart_retriever", None)
        embedder = getattr(getattr(smart, "qdrant_retriever", None), "dense_embeddings", None)
        if embedder is None:
            return _envelope(
                BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = "dense embedder not initialized (YCS lifespan failed?)",
            )
        try:
            vector = embedder.embed_query(raw_q)
        except Exception as e:
            logger.warning(f"[ycs:query:qdrant] embed failed: {type(e).__name__}: {e}")
            return _envelope(
                BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = f"embed failed: {type(e).__name__}: {str(e)[:160]}",
            )
        # YCS's collection uses NAMED vectors (`dense`/`sparse`); RR's
        # uses the default unnamed vector. Pass the right shape.
        query_vector: Any = ("dense", vector) if app == APP_YCS else vector
        try:
            results = await client.search(
                collection_name = collection,
                query_vector    = query_vector,
                limit           = limit,
                with_payload    = True,
            )
        except Exception as e:
            logger.warning(f"[ycs:query:qdrant] search failed: {type(e).__name__}: {e}")
            return _envelope(
                BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = f"{type(e).__name__}: {str(e)[:200]}",
            )
        hits = [domain.project_qdrant_point(p, app = app) for p in results]
        return _envelope(BACKEND_QDRANT, app, q, hits, total = len(hits), t0 = t0)

    # Browse-mode (empty query) — scroll a single page of points.
    try:
        records, _next = await client.scroll(
            collection_name = collection,
            limit           = limit,
            with_payload    = True,
            with_vectors    = False,
        )
    except Exception as e:
        logger.warning(f"[ycs:query:qdrant] scroll failed: {type(e).__name__}: {e}")
        return _envelope(
            BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )
    hits = [domain.project_qdrant_point(r, app = app) for r in records]
    return _envelope(BACKEND_QDRANT, app, q, hits, total = len(hits), t0 = t0)


# ====================================================================== #
# Neo4j
# ====================================================================== #
# Per-app Cypher templates. Built here (not `params.py`) because the
# strings are tightly coupled to the projection contract in `domain.py`
# (`{label, key, title, snippet, url, properties}`).
#
# `q` is empty → list-all by label, ordered by a sensible recency proxy.
# `q` is set   → CONTAINS on the main text columns (case-insensitive via
# `toLower`), unioned across labels.
#
# We deliberately use CONTAINS rather than full-text indexes here: it's
# free (no `CREATE FULLTEXT INDEX` to bootstrap on day one) and the
# corpora are small enough for a contains scan to stay sub-second. Bump
# to APOC fulltext when the dataset outgrows it.
_YCS_CYPHER_BROWSE = """
MATCH (n)
WHERE  n:Document OR n:Video OR n:Channel OR n:__Entity__
WITH   n,
       labels(n)[0] AS label,
       coalesce(n.id, n.video_id, toString(elementId(n))) AS key
RETURN label,
       key,
       coalesce(n.title, n.name, n.id, key)              AS title,
       coalesce(n.description, n.text, '')               AS snippet,
       coalesce(n.webpage_url, '')                       AS url,
       properties(n)                                     AS properties
ORDER BY label, title
LIMIT  $limit
"""

_YCS_CYPHER_SEARCH = """
MATCH (n)
WHERE  (n:Document OR n:Video OR n:Channel OR n:__Entity__)
  AND  (
        toLower(toString(coalesce(n.title, '')))        CONTAINS $needle
     OR toLower(toString(coalesce(n.name, '')))         CONTAINS $needle
     OR toLower(toString(coalesce(n.id, '')))           CONTAINS $needle
     OR toLower(toString(coalesce(n.video_id, '')))     CONTAINS $needle
     OR toLower(toString(coalesce(n.description, '')))  CONTAINS $needle
     OR toLower(toString(coalesce(n.text, '')))         CONTAINS $needle
  )
WITH   n,
       labels(n)[0] AS label,
       coalesce(n.id, n.video_id, toString(elementId(n))) AS key
RETURN label,
       key,
       coalesce(n.title, n.name, n.id, key)              AS title,
       coalesce(n.description, n.text, '')               AS snippet,
       coalesce(n.webpage_url, '')                       AS url,
       properties(n)                                     AS properties
LIMIT  $limit
"""

_RR_CYPHER_BROWSE = """
MATCH (n)
WHERE  n:Paper OR n:Author OR n:Concept OR n:Source
WITH   n, labels(n)[0] AS label, coalesce(n.id, n.name) AS key
RETURN label,
       key,
       coalesce(n.title, n.name, n.id, key)              AS title,
       coalesce(n.abstract, '')                          AS snippet,
       CASE WHEN n.id IS NOT NULL AND label = 'Paper'
            THEN 'https://arxiv.org/abs/' + toString(n.id)
            ELSE ''
       END                                               AS url,
       properties(n)                                     AS properties
ORDER BY label, coalesce(n.signal, 0) DESC, title
LIMIT  $limit
"""

_RR_CYPHER_SEARCH = """
MATCH (n)
WHERE  (n:Paper OR n:Author OR n:Concept OR n:Source)
  AND  (
        toLower(toString(coalesce(n.title, '')))     CONTAINS $needle
     OR toLower(toString(coalesce(n.name, '')))      CONTAINS $needle
     OR toLower(toString(coalesce(n.id, '')))        CONTAINS $needle
     OR toLower(toString(coalesce(n.abstract, '')))  CONTAINS $needle
  )
WITH   n, labels(n)[0] AS label, coalesce(n.id, n.name) AS key
RETURN label,
       key,
       coalesce(n.title, n.name, n.id, key)              AS title,
       coalesce(n.abstract, '')                          AS snippet,
       CASE WHEN n.id IS NOT NULL AND label = 'Paper'
            THEN 'https://arxiv.org/abs/' + toString(n.id)
            ELSE ''
       END                                               AS url,
       properties(n)                                     AS properties
LIMIT  $limit
"""


async def query_neo4j(
    *, app: str, q: str, limit: int, request: Request,
) -> QueryResponse:
    """Cypher search over the app's labels.

    Two parameter shapes: `{needle, limit}` for `_*_SEARCH` (CONTAINS
    over title/name/id/text), `{limit}` for `_*_BROWSE` (no filter).
    `needle` is pre-lowered so the Cypher only does `toLower(field)
    CONTAINS $needle` once per field — cheaper than `=~ "(?i)..."`."""
    if not is_supported(app, BACKEND_NEO4J):
        return _unsupported(BACKEND_NEO4J, app, q)

    raw_q = q.strip()
    if app == APP_RR:
        cypher = _RR_CYPHER_SEARCH if raw_q else _RR_CYPHER_BROWSE
    else:
        cypher = _YCS_CYPHER_SEARCH if raw_q else _YCS_CYPHER_BROWSE

    params: dict[str, Any] = {"limit": limit}
    if raw_q:
        params["needle"] = raw_q.lower()

    t0 = time.monotonic()
    try:
        driver = get_driver()
        async with driver.session(database = NEO4J_DATABASE) as session:
            result = await session.run(cypher, params)
            records = [dict(record) async for record in result]
    except Exception as e:
        logger.warning(f"[ycs:query:neo4j] cypher failed: {type(e).__name__}: {e}")
        return _envelope(
            BACKEND_NEO4J, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )

    hits = [domain.project_neo4j_row(r, app = app) for r in records]
    return _envelope(BACKEND_NEO4J, app, q, hits, total = len(hits), t0 = t0)


# ====================================================================== #
# Raw DSL — Phase 1 of the SOTA workbench. User-supplied DSL/Cypher/JSON.
# ====================================================================== #
def _raw_envelope(
    backend: str, app: str, t0: float,
    *,
    hits:  list[dict[str, Any]] | None = None,
    total: int | None = None,
    notes: list[str] | None = None,
    error: str | None = None,
    ok:    bool = True,
) -> RawQueryResponse:
    return RawQueryResponse(
        backend = backend,                  # type: ignore[arg-type]
        app     = app,                      # type: ignore[arg-type]
        ok      = ok,
        error   = error,
        notes   = list(notes or []),
        took_ms = int((time.monotonic() - t0) * 1000),
        total   = total,
        hits    = [RawQueryHit(**h) for h in (hits or [])],
    )


def _raw_disallowed(backend: str, app: str, msg: str) -> RawQueryResponse:
    """Validation rejection — returned with `ok=False` so the editor's
    error band lights up. HTTP layer still returns 200 to keep the
    SSE / fetch flow simple."""
    return RawQueryResponse(
        backend = backend,                  # type: ignore[arg-type]
        app     = app,                      # type: ignore[arg-type]
        ok      = False,
        error   = msg,
    )


# ---------------------------------------------------------------------- #
# Elasticsearch — POST the validated body straight at `_search` on the
# YCS metadata + transcriptions indexes. The URL path is server-pinned
# (the user never sees /controls it) so the only attack surface is the
# JSON body — and `parse_es_body` shaped it.
# ---------------------------------------------------------------------- #
async def raw_es(
    *, app: str, body_text: str, request: Request,
) -> RawQueryResponse:
    if not is_supported(app, BACKEND_ES):
        return _raw_disallowed(
            BACKEND_ES, app,
            f"{app!r} has no presence in Elasticsearch.",
        )
    try:
        parsed = parse_es_body(body_text)
    except QueryNotAllowed as e:
        return _raw_disallowed(BACKEND_ES, app, str(e))

    es: AsyncElasticsearch = get_es()
    indexes = f"{INDEX_METADATA},{INDEX_TRANSCRIPTIONS}"
    t0 = time.monotonic()
    notes: list[str] = []
    if parsed.synth_size:
        notes.append(
            f"`size` not supplied — defaulted to {parsed.body['size']}.",
        )

    try:
        response = await es.search(index = indexes, body = parsed.body)
    except Exception as e:
        logger.warning(f"[ycs:query:raw_es] failed: {type(e).__name__}: {e}")
        return _raw_envelope(
            BACKEND_ES, app, t0, ok = False,
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
        BACKEND_ES, app, t0,
        hits  = projected,
        total = total,
        notes = notes,
    )


# ---------------------------------------------------------------------- #
# Qdrant — the editor body is `{"op": ..., ...}`. Dispatch off `op`,
# pin the collection name from the (app, backend) matrix so the user
# can't query a different collection.
# ---------------------------------------------------------------------- #
async def raw_qdrant(
    *, app: str, body_text: str, request: Request,
) -> RawQueryResponse:
    if not is_supported(app, BACKEND_QDRANT):
        return _raw_disallowed(
            BACKEND_QDRANT, app,
            f"{app!r} has no presence in Qdrant.",
        )
    try:
        parsed = parse_qdrant_body(body_text)
    except QueryNotAllowed as e:
        return _raw_disallowed(BACKEND_QDRANT, app, str(e))

    from .params import APP_BACKENDS
    collection = APP_BACKENDS[app][BACKEND_QDRANT].target

    client = get_qdrant()
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
        if op == "search":
            results = await client.search(**body)
        elif op == "scroll":
            records, _ = await client.scroll(**body)
            results    = records
        elif op == "query_points":
            r = await client.query_points(**body)
            results = getattr(r, "points", r)
        elif op == "count":
            n = await client.count(**body)
            return _raw_envelope(
                BACKEND_QDRANT, app, t0,
                hits  = [{"summary": f"count={n.count}", "raw": {"count": n.count}}],
                total = int(n.count),
                notes = notes,
            )
        else:
            return _raw_disallowed(BACKEND_QDRANT, app, f"unknown op {op!r}")
    except TypeError as e:
        # Pydantic / qdrant-client kwargs mismatch on user-supplied body.
        return _raw_envelope(
            BACKEND_QDRANT, app, t0, ok = False,
            error = f"Invalid Qdrant body for op={op!r}: {e}",
            notes = notes,
        )
    except Exception as e:
        logger.warning(f"[ycs:query:raw_qdrant] failed: {type(e).__name__}: {e}")
        return _raw_envelope(
            BACKEND_QDRANT, app, t0, ok = False,
            error = f"{type(e).__name__}: {str(e)[:300]}",
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
            or payload.get("arxiv_id")
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
        BACKEND_QDRANT, app, t0,
        hits  = projected,
        total = len(projected),
        notes = notes,
    )


# ---------------------------------------------------------------------- #
# Neo4j — run user Cypher inside a read-only transaction. Driver
# distinguishes `session.execute_read(...)` from `.execute_write(...)`;
# we ALWAYS use the read variant so even a write-keyword that slipped
# past the regex (a future Cypher addition we didn't anticipate) is
# rejected by the server itself.
# ---------------------------------------------------------------------- #
async def raw_neo4j(
    *, app: str, body_text: str, request: Request,
) -> RawQueryResponse:
    if not is_supported(app, BACKEND_NEO4J):
        return _raw_disallowed(
            BACKEND_NEO4J, app,
            f"{app!r} has no presence in Neo4j.",
        )
    try:
        assert_cypher_readonly(body_text)
    except QueryNotAllowed as e:
        return _raw_disallowed(BACKEND_NEO4J, app, str(e))

    t0 = time.monotonic()
    try:
        driver = get_driver()
        async with driver.session(
            database = NEO4J_DATABASE, default_access_mode = "READ",
        ) as session:
            result = await session.run(body_text)
            records = [dict(r) async for r in result]
    except Exception as e:
        logger.warning(f"[ycs:query:raw_neo4j] failed: {type(e).__name__}: {e}")
        return _raw_envelope(
            BACKEND_NEO4J, app, t0, ok = False,
            error = f"{type(e).__name__}: {str(e)[:400]}",
        )

    projected: list[dict[str, Any]] = []
    for r in records:
        # Neo4j returns rows of named columns. Build a one-line summary
        # by joining the first 2 non-None values; the raw dict goes into
        # `raw` for the renderer (graph / table / json toggle).
        bits: list[str] = []
        for k, v in r.items():
            if v is None:
                continue
            s = str(v)
            bits.append(f"{k}={s[:60]}")
            if len(bits) >= 2:
                break
        projected.append({
            "summary": "  ".join(bits) if bits else "(empty row)",
            "raw":     _neo4j_jsonify(r),
        })
    return _raw_envelope(
        BACKEND_NEO4J, app, t0,
        hits  = projected,
        total = len(projected),
    )


# Neo4j driver returns Node / Relationship objects that aren't directly
# JSON-serializable. Convert each row into a plain `dict` of primitives
# preserving the graph-shape under `_node` / `_relationship` keys so the
# frontend can build a Cytoscape graph view from them.
def _neo4j_jsonify(row: dict) -> dict:
    from neo4j.graph import Node, Path, Relationship
    out: dict = {}
    for k, v in row.items():
        out[k] = _neo4j_value(v, Node = Node, Rel = Relationship, Path = Path)
    return out


# ====================================================================== #
# AI text-to-DSL — Phase 4
# ====================================================================== #
# Why inherit AsyncCallbackHandler: the runtime CallbackManager checks
# `isinstance(handler, AsyncCallbackHandler)` to decide whether to AWAIT
# the handler's async methods. A plain class with `async def` methods
# triggers `RuntimeWarning: coroutine 'ahandle_event' was never awaited`
# (observed in the pod 2026-06-16) and silently drops the model
# capture. Inheriting the real base class fixes both.
from langchain_core.callbacks import AsyncCallbackHandler


class _ModelCapture(AsyncCallbackHandler):
    """Captures the FGTS-VA-selected REAL deployment id.

    The rotator's `_RotatorAutoRetryRouter._create_chat_result` writes
    the deployment that actually answered (e.g.
    `nvidia_nim/openai/gpt-oss-120b`) into both `llm_output["model_name"]`
    AND `AIMessage.response_metadata["model_name"]` — see
    `domains/llm/rotator/chain/service.py:1080-1133`. We read from those
    two sources because the chain's `invocation_params["model"]` only
    holds the group alias (`dd-all`), which is what we DON'T want to
    show.

    Sources of truth, in order of preference:
      1. `on_llm_end(response)` → `response.llm_output["model_name"]`
      2. `chunk.response_metadata["model_name"]` (per-chunk in _stream)
      3. `on_chat_model_start` `invocation_params["model"]` — fallback
         only when it's NOT a group alias.
    """
    # Group aliases the rotator uses — NOT what we want to display.
    _GROUP_ALIASES = frozenset({
        "dd-all", "dd-grader", "dd-synth-write", "dd-planner",
        "ycs-query-ai", "rr-strong",
    })

    def __init__(self) -> None:
        super().__init__()
        self.model: str | None = None
        self.attempts: list[str] = []

    @classmethod
    def _is_group_alias(cls, name: str | None) -> bool:
        if not name:
            return True
        n = str(name).lower().strip()
        if n in cls._GROUP_ALIASES:
            return True
        # General pattern: group aliases use `<context>-<role>` with
        # only hyphens and no provider/model slash. Real deployments
        # ALWAYS contain a `/` (`nvidia_nim/...`, `groq/...`,
        # `gemini/...`).
        return "/" not in n and n.startswith(("dd-", "rr-", "ycs-"))

    def _absorb(self, candidate: str | None) -> None:
        """Take a candidate model string and store it only if it looks
        like a real deployment (filters group aliases)."""
        if not candidate:
            return
        c = str(candidate)
        if self._is_group_alias(c):
            # Only stash as a last-resort fallback; do NOT overwrite a
            # better-quality capture we already have.
            if self.model is None:
                self.model = c
            return
        # Real deployment — always wins.
        self.model = c
        self.attempts.append(c)

    @staticmethod
    def _model_from_start(serialized: dict | None, kwargs: dict) -> str | None:
        params = kwargs.get("invocation_params") or {}
        return (
            params.get("model")
            or params.get("model_name")
            or (serialized or {}).get("name")
        )

    async def on_chat_model_start(self, serialized, messages, **kwargs):
        self._absorb(self._model_from_start(serialized, kwargs))

    async def on_llm_start(self, serialized, prompts, **kwargs):
        self._absorb(self._model_from_start(serialized, kwargs))

    async def on_llm_end(self, response, **kwargs):
        """Authoritative pass — the rotator stamps the real deployment
        id into the response. This fires AFTER streaming completes but
        BEFORE our generator's `done` frame, so the model chip flips
        from any prior group-alias fallback to the real arm just in
        time."""
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            real = llm_output.get("model_name") or llm_output.get("model")
            if not real:
                gens = getattr(response, "generations", None) or []
                if gens and gens[0]:
                    g0 = gens[0][0]
                    gi = getattr(g0, "generation_info", None) or {}
                    real = gi.get("model_name") or gi.get("model")
                    if not real:
                        msg = getattr(g0, "message", None)
                        if msg is not None:
                            rm = getattr(msg, "response_metadata", None) or {}
                            real = rm.get("model_name") or rm.get("model")
            self._absorb(real)
        except Exception:
            pass


async def ai_generate_stream(
    *, backend: str, app: str, user_prompt: str, previous: str, request: Request,
):
    """Async-generator that yields {"event": ..., "data": ...} dicts the
    router serializes to SSE frames.

    Pipeline:
      1. Fetch schema (Phase 3 cached) — best-effort; if it fails we
         still generate with a fallback hint.
      2. Build the generation prompt (rules + schema + few-shot +
         previous editor content).
      3. Stream from `app.state.llm` — every token is forwarded to the
         client as `data: {"chunk": "..."}`.
      4. After the stream completes, run the same safety guard the Run
         path uses. On rejection, ONE self-repair retry (full re-generate
         with the error fed in).
      5. Emit a terminal `data: {"event": "done", "ok": ..., "final":
         "..."}` frame so the client can replace the editor with the
         clean text (vs. the streamed-with-self-repair chatter)."""
    import json as _json

    from .examples import EXAMPLES_BY_BACKEND
    from .prompts  import build_generate_prompt, build_repair_prompt

    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        yield {"data": _json.dumps({
            "event": "error",
            "error": "LLM rotator not initialized (YCS lifespan failed).",
        })}
        return

    # 1. Schema. Best-effort — degrades to None on any failure.
    schema_payload = None
    try:
        if backend == BACKEND_ES:
            schema_payload = await get_es_schema(request = request)
        elif backend == BACKEND_QDRANT:
            schema_payload = await get_qdrant_schema(request = request)
        elif backend == BACKEND_NEO4J:
            schema_payload = await get_neo4j_schema(request = request)
    except Exception as e:
        logger.warning(f"[ycs:query:ai] schema fetch failed: {type(e).__name__}: {e}")

    schema_obj = None
    if isinstance(schema_payload, dict):
        # The cached-response wrapper adds `cached_at` at the top level;
        # the prompt builders want the raw schema dict. Strip the
        # wrapper key.
        schema_obj = {k: v for k, v in schema_payload.items() if k != "cached_at"}

    examples = EXAMPLES_BY_BACKEND.get(backend, [])

    async def _stream(prompt_text: str):
        """Yield `(kind, payload, accumulated)` tuples — `kind` is one of
        {`yield`, `model`, `done`, `error`}. We never `return value`-out
        of this async generator (Python forbids it); the accumulated
        text is threaded through the tuple so the caller has it on the
        terminal `done` frame.

        Model capture: `_ModelCapture` is a LangChain `AsyncCallbackHandler`
        that pulls the bandit-selected model_id from `on_chat_model_start`
        / `on_llm_start`. The rotator's `ChatLiteLLMRouter` runs the
        FGTS-VA bandit pick BEFORE the model call, so by the time the
        first chunk arrives, `capture.model` is set. We emit a `model`
        tuple the first time it's populated — the orchestrator turns
        that into an SSE frame for the AI panel."""
        accumulated = ""
        capture = _ModelCapture()
        try:
            astream = llm.astream(
                prompt_text, config = {"callbacks": [capture]},
            )
        except Exception as e:
            yield ("error", f"{type(e).__name__}: {e}", accumulated)
            return
        last_emitted: str | None = None
        try:
            async for chunk in astream:
                # Pull the real deployment id straight from the chunk's
                # response_metadata — the rotator's
                # `_create_chat_result` stamps it on every chunk
                # message (see service.py:1114-1129). This bypasses
                # `invocation_params["model"]` which would only carry
                # the group alias ("dd-all").
                rm = getattr(chunk, "response_metadata", None) or {}
                cm = rm.get("model_name") or rm.get("model")
                if cm:
                    capture._absorb(cm)

                text = getattr(chunk, "content", "") or ""
                if isinstance(text, list):
                    text = " ".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in text
                    )
                text = str(text)

                # Emit the model whenever it CHANGES — initially we
                # may have only the group alias (fallback); once a
                # chunk carries the real deployment we flip the chip
                # in place without the user noticing.
                if capture.model and capture.model != last_emitted:
                    last_emitted = capture.model
                    yield ("model", capture.model, accumulated)

                if not text:
                    continue
                accumulated += text
                payload = _json.dumps({"event": "chunk", "data": text})
                yield ("yield", payload, accumulated)
        except Exception as e:
            yield ("error", f"{type(e).__name__}: {e}", accumulated)
            return
        # Final pass — `on_llm_end` (authoritative) fires AFTER the
        # async iterator exits, so by here capture.model holds the
        # rotator's resolved deployment id. Re-emit if it changed.
        if capture.model and capture.model != last_emitted:
            yield ("model", capture.model, accumulated)
        yield ("done", "", accumulated)

    # First pass.
    prompt1 = build_generate_prompt(
        backend     = backend,
        user_prompt = user_prompt,
        schema      = schema_obj,
        examples    = examples,
        previous    = previous,
    )
    yield {"data": _json.dumps({"event": "start", "phase": "generate"})}
    acc = ""
    stream_err: str | None = None
    async for kind, payload, txt in _stream(prompt1):
        if kind == "yield":
            yield {"data": payload}
        elif kind == "model":
            yield {"data": _json.dumps({"event": "model", "model": payload})}
        elif kind == "error":
            stream_err = payload
            acc = txt
            break
        elif kind == "done":
            acc = txt
            break

    if stream_err is not None:
        yield {"data": _json.dumps({
            "event": "done", "ok": False,
            "error": stream_err, "final": acc,
        })}
        return

    final = _post_clean(acc, backend = backend)
    ok, err = _check_with_safety(final, backend = backend)
    if not ok:
        # Log the rejection so the next Generate error in the pod is
        # diagnosable from `kubectl logs` without a transcript-replay.
        # Capped at 1500 chars to bound log volume.
        logger.warning(
            "[ycs:query:ai] first-pass rejected (%s): %s — body[:1500]=%r",
            backend, err, final[:1500],
        )
        # Self-repair — one retry.
        yield {"data": _json.dumps({
            "event": "repair",
            "error": err or "(unknown parse error)",
        })}
        prompt2 = build_repair_prompt(
            backend     = backend,
            user_prompt = user_prompt,
            attempt     = final,
            error       = err or "",
            schema      = schema_obj,
            examples    = examples,
        )
        acc2 = ""
        async for kind, payload, txt in _stream(prompt2):
            if kind == "yield":
                yield {"data": payload}
            elif kind == "model":
                yield {"data": _json.dumps({"event": "model", "model": payload})}
            elif kind == "error":
                stream_err = payload
                acc2 = txt
                break
            elif kind == "done":
                acc2 = txt
                break
        if stream_err is not None:
            yield {"data": _json.dumps({
                "event": "done", "ok": False,
                "error": stream_err, "final": _post_clean(acc2, backend = backend),
            })}
            return
        final = _post_clean(acc2, backend = backend)
        ok, err = _check_with_safety(final, backend = backend)
        if not ok:
            logger.warning(
                "[ycs:query:ai] self-repair ALSO rejected (%s): %s — body[:1500]=%r",
                backend, err, final[:1500],
            )

    yield {"data": _json.dumps({
        "event": "done",
        "ok":    ok,
        "error": err,
        "final": final,
    })}


def _post_clean(text: str, *, backend: str) -> str:
    """Strip markdown fences + leading/trailing prose the model sometimes
    can't help adding, then PRETTY-FORMAT the result so the editor
    shows it indented + readable.

      - JSON backends → balance braces to extract the JSON object,
        then `json.loads` + `json.dumps(indent=2)` so the final body
        is canonical-pretty (2-space indent, stable key order, no
        trailing whitespace). Falls back to the raw extract if the
        text doesn't parse — that path also gets surfaced by the
        safety guard and triggers a self-repair retry.
      - Cypher → rstrip per line, collapse blank-line runs > 2,
        and strip leading/trailing whitespace. The few-shot
        exemplars already teach the LLM the line-per-clause style;
        we just polish what came back.
    """
    if not text:
        return ""
    s = text.strip()

    # Strip surrounding code fences (```json / ```cypher / ``` / etc.).
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    s = s.strip()

    if backend in (BACKEND_ES, BACKEND_QDRANT):
        extracted = _extract_balanced_json(s)
        try:
            obj = json.loads(extracted)
        except json.JSONDecodeError:
            # Common LLM mistake: trailing commas before `}` / `]`.
            # Standard JSON forbids them; one permissive sweep recovers
            # the most frequent failure mode without pulling in json5.
            relaxed = re.sub(r",(\s*[}\]])", r"\1", extracted)
            try:
                obj = json.loads(relaxed)
            except json.JSONDecodeError:
                # Truly malformed — return raw so the safety guard
                # surfaces a precise error and self-repair fires.
                return extracted
        # ensure_ascii=False so unicode survives the round-trip;
        # indent=2 matches the scaffold templates in editor.js so the
        # editor renders cleanly after replacement.
        return json.dumps(obj, indent = 2, ensure_ascii = False)

    if backend == BACKEND_NEO4J:
        return _format_cypher(_extract_cypher(s))

    return s


def _extract_cypher(s: str) -> str:
    """Pull just the Cypher out of a response that may also contain
    prose, fenced blocks, or other commentary.

    Real-world failure mode (2026-06-16, "best graph about Brasil"):
    the LLM wrote prose around the Cypher — e.g. "I'll create a query
    that…" / "this will delete duplicates from the result". The prose
    contained `create` / `delete`, which the safety regex flagged as
    write ops, rejecting an otherwise-valid READ-only query.

    Extraction strategy (each step strictly stronger than the last):
      1. Fenced block ANYWHERE — triple-backtick blocks, optionally
         tagged ``cypher`` / ``cql``. Strong signal; almost never
         matches inside natural prose.
      2. Per-LINE scan for a line that STARTS with a Cypher clause
         pattern (MATCH `(`, OPTIONAL MATCH `(`, CALL foo.bar`(`, or
         WITH/UNWIND/RETURN + identifier). The next-token requirement
         filters out prose words like "match the entity then …".
      3. Last-resort: substring search for `MATCH (` or `OPTIONAL
         MATCH (` with the opening paren mandatory — that's about as
         strong a Cypher signal as you can get in a single token.
      4. Failing all three, return the input verbatim and let the
         safety regex deliver a precise rejection message."""
    if not s:
        return s
    # 1. Fenced extraction.
    fence = re.search(
        r"```(?:cypher|cql)?\s*\n?(.*?)```",
        s, flags = re.DOTALL | re.IGNORECASE,
    )
    if fence:
        return fence.group(1).strip()
    # 2. Per-line scan for the FIRST line that opens a Cypher clause.
    #    The next-token after the keyword (`(` or `\S`) is what
    #    distinguishes "MATCH (x)" from a prose word like "match".
    _CYPHER_LINE_START = re.compile(
        r"^\s*(?:"
        r"MATCH\s*\("                       # MATCH (
        r"|OPTIONAL\s+MATCH\s*\("           # OPTIONAL MATCH (
        r"|CALL\s+[A-Za-z_][\w.]*\s*\("     # CALL apoc.foo(
        r"|WITH\s+\S"                       # WITH x
        r"|UNWIND\s+\S"                     # UNWIND list
        r"|RETURN\s+\S"                     # RETURN x
        r")",
        flags = re.IGNORECASE,
    )
    lines = s.split("\n")
    for i, line in enumerate(lines):
        if _CYPHER_LINE_START.match(line):
            return "\n".join(lines[i:]).strip()
    # 3. Substring fallback — only the strongest signals (`KEYWORD (`)
    #    to avoid grabbing prose words.
    m = re.search(
        r"\b(?:MATCH|OPTIONAL\s+MATCH)\s*\(",
        s, flags = re.IGNORECASE,
    )
    if m:
        return s[m.start():].strip()
    # 4. Give up — let safety speak.
    return s


def _extract_balanced_json(s: str) -> str:
    """Walk from the first `{` to its matching `}`, ignoring braces
    inside string literals (handles escaped quotes). Returns the
    enclosed JSON or `s` unchanged if no balanced object is found —
    cheap defense against prose around the JSON body."""
    start = s.find("{")
    if start < 0:
        return s
    depth = 0
    in_str = False
    esc    = False
    for i, ch in enumerate(s[start:], start = start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start: i + 1]
    return s


def _format_cypher(s: str) -> str:
    """Cypher polish — inject newlines before major clause keywords so
    a single-line model response renders multi-line + legible.

    Pipeline:
      1. Normalize line endings + strip.
      2. PROTECT string literals + `//` + `/* */` comments by swapping
         them out for placeholders — so we never insert newlines inside
         a quoted token (e.g. `RETURN \"what to RETURN\"` shouldn't
         break in the middle of the string).
      3. Inject a newline before each major Cypher keyword that's
         preceded by inline whitespace. Compound keywords first
         (`OPTIONAL MATCH`, `UNION ALL`, `ORDER BY`) so they win over
         the bare single-word variants.
      4. Restore the protected literals.
      5. rstrip per line + collapse blank-line runs of 3+.

    Case-preserving: the matched keyword text is reused verbatim in the
    replacement (the LLM might output lowercase Cypher; we don't
    silently upper-case it)."""
    if not s.strip():
        return ""
    src = s.replace("\r\n", "\n").strip()

    # 1. Protect strings + comments.
    placeholders: list[str] = []
    def _protect(match: "re.Match[str]") -> str:
        placeholders.append(match.group(0))
        return f"\x00P{len(placeholders) - 1}\x00"

    _protect_re = re.compile(
        r"//[^\n]*"                       # line comment
        r"|/\*.*?\*/"                     # block comment (non-greedy)
        r"|'(?:\\.|[^'\\])*'"             # single-quoted string
        r"|\"(?:\\.|[^\"\\])*\""          # double-quoted string
        r"|`(?:\\.|[^`\\])*`",            # back-tick identifier
        flags = re.DOTALL,
    )
    protected = _protect_re.sub(_protect, src)

    # 2. Inject newlines before major clauses. SINGLE alternation regex
    #    with longest-first ordering so `OPTIONAL MATCH` wins over the
    #    bare `MATCH` rule and doesn't get split in half (the two-pass
    #    version produced `OPTIONAL\nMATCH`). `re.sub` does left-to-
    #    right non-overlapping matching, so once "OPTIONAL MATCH" is
    #    consumed at position N the next search resumes past the
    #    compound keyword.
    _KEYWORDS = (
        "OPTIONAL MATCH", "UNION ALL", "ORDER BY",
        "MATCH", "WHERE", "WITH", "RETURN", "LIMIT", "SKIP",
        "UNION", "UNWIND", "CALL", "YIELD",
    )
    _alts = sorted(
        # Spaces in keywords become `\s+` so `OPTIONAL\nMATCH` or
        # `OPTIONAL  MATCH` (multiple spaces) still match as one
        # compound clause.
        (kw.replace(" ", r"\s+") for kw in _KEYWORDS),
        key = len, reverse = True,
    )
    _all_kw_re = re.compile(
        r"(?<!\n)[ \t]+(" + "|".join(_alts) + r")\b",
        flags = re.IGNORECASE,
    )
    protected = _all_kw_re.sub(lambda m: "\n" + m.group(1), protected)

    # 3. Restore protected spans.
    out = re.sub(
        r"\x00P(\d+)\x00",
        lambda m: placeholders[int(m.group(1))],
        protected,
    )

    # 4. Final normalization.
    out = "\n".join(line.rstrip() for line in out.split("\n"))
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _check_with_safety(text: str, *, backend: str) -> tuple[bool, str | None]:
    """Apply the same safety guards the Run path uses — so the AI
    output passes the EXACT same gate as user-typed content. Returns
    `(ok, error)`."""
    try:
        if backend == BACKEND_NEO4J:
            assert_cypher_readonly(text)
        elif backend == BACKEND_ES:
            parse_es_body(text)
        elif backend == BACKEND_QDRANT:
            parse_qdrant_body(text)
    except QueryNotAllowed as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, None


# ====================================================================== #
# Schema discovery — Phase 3
# ====================================================================== #
# Cached 5 min in Redis so backend-switch / editor autocomplete / AI
# prompt-building can all hit it cheaply. Cache key is namespaced by
# backend; the YCS pin means we don't need an `app` component yet.
# 2026-06-16: two-layer schema (declared floor + live overlay). The
# declared floor guarantees the AI prompt sees the full structural
# shape even on empty stores; the live layer enriches it with real
# samples, observed counts, and relationship patterns the writer code
# couldn't predict (e.g. LLM-generated inter-entity relationship
# names). Cache-key bumped `:v3` to invalidate any `:v2` blobs.
_SCHEMA_TTL_S = 300
_SCHEMA_KEY = "ycs:query:schema:{backend}:v3"


async def _schema_cached(
    *, backend: str, request: Request, refresh: bool,
    builder,
) -> dict[str, Any]:
    """Generic Redis read-through cache for the per-backend builder.

    `builder` is a `Callable[[], Awaitable[dict]]` that fetches the live
    schema; we never call it twice in parallel under cache contention
    (the cost of a duplicate refresh is bounded so we don't bother with
    a distributed lock)."""
    import json as _json
    redis_aio = getattr(request.app.state, "redis_aio", None)
    key = _SCHEMA_KEY.format(backend = backend)
    if redis_aio is not None and not refresh:
        try:
            raw = await redis_aio.get(key)
        except Exception:
            raw = None
        if raw:
            try:
                obj = _json.loads(raw)
                return obj
            except Exception:
                pass
    obj = await builder()
    obj["cached_at"] = int(time.time())
    if redis_aio is not None:
        try:
            await redis_aio.set(key, _json.dumps(obj), ex = _SCHEMA_TTL_S)
        except Exception as e:
            logger.warning(f"[ycs:query:schema] redis set failed: {e}")
    return obj


# ---------------------------------------------------------------------- #
# Elasticsearch — GET _mapping + doc counts per index.
# ---------------------------------------------------------------------- #
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
    es = get_es()
    indices = [INDEX_METADATA, INDEX_TRANSCRIPTIONS]
    out: dict[str, Any] = {"indices": {}}
    for idx in indices:
        try:
            mapping = await es.indices.get_mapping(index = idx)
            stats   = await es.indices.stats(index = idx, metric = "docs")
        except Exception as e:
            out["indices"][idx] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            continue
        doc_count = (
            stats.get("indices", {}).get(idx, {})
            .get("primaries", {}).get("docs", {}).get("count", 0)
        )
        mappings = mapping.get(idx, {}).get("mappings", {})
        props    = mappings.get("properties", {}) or {}

        # Sample 2 docs — small, kept compact in the prompt. `_source`
        # truncated per-field below (text/content can be huge).
        samples: list[dict[str, Any]] = []
        try:
            s_resp = await es.search(
                index = idx,
                size  = 2,
                query = {"match_all": {}},
            )
            for h in s_resp.get("hits", {}).get("hits", []):
                samples.append({
                    "_id":     h.get("_id"),
                    "_source": _truncate_doc(h.get("_source", {}) or {}),
                })
        except Exception as e:
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
                a_resp = await es.search(
                    index = idx, size = 0, aggs = aggs,
                )
                buckets = a_resp.get("aggregations", {}) or {}
                for i, name in enumerate(keyword_fields):
                    raw = buckets.get(f"v_{i}", {}).get("buckets", []) or []
                    vals = [str(b.get("key")) for b in raw if b.get("key") not in (None, "")]
                    if vals:
                        field_values[name] = vals
            except Exception as e:
                logger.debug(f"[ycs:query:schema:es] terms agg failed for {idx}: {e}")

        out["indices"][idx] = {
            "doc_count":    int(doc_count or 0),
            "mappings":     mappings,
            "samples":      samples,
            "field_values": field_values,
        }
    return out


def _truncate_doc(src: dict, *, max_field_chars: int = 240) -> dict:
    """Trim long string fields in a sample `_source` so the prompt
    stays under-budget. Lists are shallow-truncated to 3 items."""
    out: dict[str, Any] = {}
    for k, v in (src or {}).items():
        if isinstance(v, str):
            out[k] = v if len(v) <= max_field_chars else v[:max_field_chars] + "…"
        elif isinstance(v, list):
            out[k] = v[:3]
        elif isinstance(v, dict):
            out[k] = {ik: iv for ik, iv in list(v.items())[:6]}
        else:
            out[k] = v
    return out


async def _build_es_schema() -> dict[str, Any]:
    """Two-layer ES schema: declared floor + live overlay.

    Declared floor (`declared.declared_es_schema`) is sourced from
    `infra/elasticsearch/mappings.py` so an empty cluster / outage
    still surfaces the full mapping. Live overlay merges per-index
    samples + field_values + observed mappings (in case ES has drifted
    from what we declared) + doc_count."""
    from .declared import declared_es_schema
    declared = declared_es_schema()
    try:
        live = await _build_es_schema_live()
    except Exception as e:
        logger.warning(f"[ycs:query:schema:es] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return _merge_es_schema(declared, live)


def _merge_es_schema(declared: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Per-index overlay. Declared mappings are kept ONLY when live is
    missing/erroring (live wins on mappings to track schema drift);
    samples + field_values + doc_count come from live."""
    out: dict[str, Any] = {"indices": {}}
    for name, idx in (declared.get("indices") or {}).items():
        out["indices"][name] = dict(idx)
    for name, idx in (live.get("indices") or {}).items():
        cur = out["indices"].get(name, {})
        if idx.get("error"):
            # Live had an error for this index — keep declared floor
            cur["error"] = idx["error"]
            out["indices"][name] = cur
            continue
        if idx.get("mappings"):
            cur["mappings"] = idx["mappings"]
        cur["doc_count"]    = idx.get("doc_count", cur.get("doc_count", 0))
        cur["samples"]      = idx.get("samples")      or cur.get("samples", [])
        cur["field_values"] = idx.get("field_values") or cur.get("field_values", {})
        out["indices"][name] = cur
    return out


async def get_es_schema(*, request: Request, refresh: bool = False) -> dict[str, Any]:
    return await _schema_cached(
        backend = BACKEND_ES, request = request, refresh = refresh,
        builder = _build_es_schema,
    )


# ---------------------------------------------------------------------- #
# Qdrant — collection info (vector params + payload schema) + point count.
# ---------------------------------------------------------------------- #
async def _build_qdrant_schema_live() -> dict[str, Any]:
    """Qdrant live schema — overlay for the two-layer merge.

    Returns the per-collection vectors_config + declared payload_schema
    + observed_payload_keys (union of keys across sampled payloads) +
    3 sample payloads. Degrades to empty when the collection is empty;
    `_build_qdrant_schema` merges this on top of the declared floor."""
    from .params import APP_BACKENDS
    collection = APP_BACKENDS[APP_YCS][BACKEND_QDRANT].target
    client = get_qdrant()
    try:
        info = await client.get_collection(collection_name = collection)
    except Exception as e:
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

    # Sample 3 payloads so the AI sees ACTUAL keys (the declared
    # payload_schema only carries indexed keys; some payloads have
    # additional unindexed keys we want to surface).
    samples: list[dict[str, Any]] = []
    try:
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
                "payload": _truncate_doc(payload),
            })
    except Exception as e:
        logger.debug(f"[ycs:query:schema:qdrant] sample scroll failed: {e}")

    # Union of payload keys observed across samples (catches keys
    # missing from the declared payload_schema).
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

    Declared floor (`declared.declared_qdrant_schema`) lists the FULL
    canonical payload keys from `domains/ycs/ingestion/domain.py:
    build_payload`. Live overlay adds the actual points_count + real
    sample payloads (so the LLM sees concrete values, not just keys)."""
    from .declared import declared_qdrant_schema
    declared = declared_qdrant_schema()
    try:
        live = await _build_qdrant_schema_live()
    except Exception as e:
        logger.warning(f"[ycs:query:schema:qdrant] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return _merge_qdrant_schema(declared, live)


def _merge_qdrant_schema(declared: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Per-collection overlay. Declared payload-key list is the floor
    (union with whatever the live scroll observed). Live vectors_config
    + samples + points_count overlay. `text_indexed_fields` is derived
    from the declared payload_schema where data_type == 'text'; live
    can add to it if a field has a text index in production that the
    declared floor doesn't list."""
    declared_cols = {c["name"]: c for c in (declared.get("collections") or [])}
    live_cols     = {c["name"]: c for c in (live.get("collections")     or [])}
    out_cols: list[dict[str, Any]] = []
    for name in (set(declared_cols) | set(live_cols)):
        d = declared_cols.get(name, {})
        l = live_cols.get(name, {})
        if l.get("error"):
            merged = dict(d)
            merged["error"] = l["error"]
            out_cols.append(merged)
            continue
        # `text_indexed_fields` = union of declared + any live field
        # whose payload_schema data_type starts with "text".
        text_idx: set[str] = set(d.get("text_indexed_fields") or [])
        text_idx.update(l.get("text_indexed_fields") or [])
        for field, cfg in (l.get("payload_schema") or {}).items():
            dt = str((cfg or {}).get("data_type") or "").lower()
            if dt.startswith("text"):
                text_idx.add(field)
        merged = {
            "name":           name,
            "points_count":   l.get("points_count", d.get("points_count", 0)),
            "vectors_config": l.get("vectors_config") or d.get("vectors_config"),
            "payload_schema": l.get("payload_schema") or d.get("payload_schema") or {},
            "observed_payload_keys": sorted(
                set(d.get("observed_payload_keys") or [])
                | set(l.get("observed_payload_keys") or [])
            ),
            "text_indexed_fields": sorted(text_idx),
            "samples": l.get("samples") or d.get("samples") or [],
        }
        out_cols.append(merged)
    return {"collections": out_cols}


async def get_qdrant_schema(*, request: Request, refresh: bool = False) -> dict[str, Any]:
    return await _schema_cached(
        backend = BACKEND_QDRANT, request = request, refresh = refresh,
        builder = _build_qdrant_schema,
    )


# ---------------------------------------------------------------------- #
# Neo4j — db.labels + db.relationshipTypes + db.schema.nodeTypeProperties.
# All read procedures, no APOC dep.
# ---------------------------------------------------------------------- #
_SCHEMA_CYPHER_LABELS = "CALL db.labels() YIELD label RETURN collect(label) AS labels"
_SCHEMA_CYPHER_RELS   = "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS rels"
_SCHEMA_CYPHER_PROPS  = (
    "CALL db.schema.nodeTypeProperties() "
    "YIELD nodeLabels, propertyName, propertyTypes "
    "RETURN nodeLabels, propertyName, propertyTypes "
    "ORDER BY nodeLabels, propertyName"
)
# Real (srcLabel)-[REL]->(dstLabel) patterns, with cardinality. Sampled
# from up to 5000 random rels per type — gives the AI prompt the actual
# connectivity shape without scanning the whole graph. The MERCHANDISE
# of `db.schema.visualization()` would be cheaper but it's APOC-only.
_SCHEMA_CYPHER_REL_PATTERNS = """
MATCH (a)-[r]->(b)
WITH labels(a)[0] AS src, type(r) AS rel, labels(b)[0] AS dst, count(*) AS n
WHERE src IS NOT NULL AND dst IS NOT NULL
RETURN src, rel, dst, n
ORDER BY n DESC
LIMIT 50
"""
# Sample 3 nodes per label with their properties (truncated). Driven
# by `db.labels()` results — one tiny Cypher per label.
_SCHEMA_CYPHER_LABEL_SAMPLES = """
MATCH (n)
WHERE labels(n)[0] = $label
RETURN n
LIMIT 3
"""


async def _build_neo4j_schema_live() -> dict[str, Any]:
    """Neo4j live schema — overlay layer for the two-layer merge.

    Output shape (matches the declared floor exactly so merging is a
    straight union):
      · labels                 — list of label names that have ≥1 node
      · relationship_types     — list of rel types with ≥1 instance
      · node_properties        — {label: [{name, types}]}
      · relationship_patterns  — [{src, rel, dst, count}] (actual
                                  observed (src)-[REL]->(dst) triples,
                                  Cypher-runnable, ranked by frequency)
      · node_samples           — {label: [{ id, properties }]} (3 per label)

    EVERY field degrades to empty on an empty Neo4j (the schema procs
    are data-derived), which is exactly the case the declared floor
    is there to cover. Read-only — uses `default_access_mode="READ"`
    defense in depth."""
    driver = get_driver()
    out: dict[str, Any] = {
        "labels": [],
        "relationship_types": [],
        "node_properties": {},
        "relationship_patterns": [],
        "node_samples": {},
    }
    try:
        async with driver.session(
            database = NEO4J_DATABASE, default_access_mode = "READ",
        ) as session:
            r = await session.run(_SCHEMA_CYPHER_LABELS)
            row = await r.single()
            out["labels"] = list(row["labels"]) if row else []

            r = await session.run(_SCHEMA_CYPHER_RELS)
            row = await r.single()
            out["relationship_types"] = list(row["rels"]) if row else []

            r = await session.run(_SCHEMA_CYPHER_PROPS)
            props_by_label: dict[str, list[dict[str, Any]]] = {}
            async for row in r:
                labels = list(row["nodeLabels"] or [])
                name   = row["propertyName"]
                types  = list(row["propertyTypes"] or [])
                if not name:
                    continue
                for lab in labels:
                    props_by_label.setdefault(lab, []).append({
                        "name":  name,
                        "types": types,
                    })
            out["node_properties"] = props_by_label

            # Real connectivity — frequency-ranked. This is the
            # single highest-ROI add for AI grounding: the LLM sees
            # `(Document)-[MENTIONS]->(__Entity__) x 18402` rather
            # than guessing that `:MENTIONS` might exist.
            try:
                r = await session.run(_SCHEMA_CYPHER_REL_PATTERNS)
                async for row in r:
                    out["relationship_patterns"].append({
                        "src":   row["src"],
                        "rel":   row["rel"],
                        "dst":   row["dst"],
                        "count": int(row["n"] or 0),
                    })
            except Exception as e:
                logger.debug(f"[ycs:query:schema:neo4j] rel patterns failed: {e}")

            # Sample 3 nodes per label. Capped to first 10 labels to
            # keep schema size bounded; the rest get an empty list.
            for lab in (out["labels"] or [])[:10]:
                try:
                    r = await session.run(
                        _SCHEMA_CYPHER_LABEL_SAMPLES, {"label": lab},
                    )
                    samples: list[dict[str, Any]] = []
                    async for row in r:
                        n = row["n"]
                        props = dict(n) if n else {}
                        samples.append({
                            "id":         n.element_id if n else None,
                            "properties": _truncate_doc(props),
                        })
                    out["node_samples"][lab] = samples
                except Exception as e:
                    logger.debug(
                        f"[ycs:query:schema:neo4j] samples for {lab!r} failed: {e}",
                    )
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


async def _build_neo4j_schema() -> dict[str, Any]:
    """Two-layer Neo4j schema: declared floor + live overlay.

    Highest-value case for the two-layer split: Neo4j's introspection
    procedures (`db.labels()`, `db.relationshipTypes()`,
    `db.schema.nodeTypeProperties()`) are entirely data-derived — an
    empty graph returns NOTHING. Without the declared floor the AI has
    zero structural grounding on day-zero or after a wipe.

    Declared floor (`declared.declared_neo4j_schema`) is sourced from
    the actual writer code in `domains/ycs/graph_builder/` so it
    matches what the LLMGraphTransformer + the YCS-specific
    `build_video_metadata_graph` produce."""
    from .declared import declared_neo4j_schema
    declared = declared_neo4j_schema()
    try:
        live = await _build_neo4j_schema_live()
    except Exception as e:
        logger.warning(f"[ycs:query:schema:neo4j] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return _merge_neo4j_schema(declared, live)


def _merge_neo4j_schema(declared: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Union of labels + rel types; union of properties per label; merge
    of relationship_patterns marking declared-but-unobserved with
    `count=null`, declared-AND-observed with the live count, and
    live-only (LLM-invented inter-entity rels) untouched."""
    if live.get("error") and not (live.get("labels") or live.get("relationship_types")):
        # Live call totally failed — pass declared with the error annotated.
        out = dict(declared)
        out["error"] = live["error"]
        return out

    # 1. Labels + relationship types — union.
    labels = sorted(set(declared.get("labels") or []) | set(live.get("labels") or []))
    rels   = sorted(
        set(declared.get("relationship_types") or [])
        | set(live.get("relationship_types") or [])
    )

    # 2. Node properties — union per label (dedupe by name).
    node_props: dict[str, list[dict[str, Any]]] = {}
    for source in (declared.get("node_properties") or {}, live.get("node_properties") or {}):
        for label, props in source.items():
            current = {p["name"]: p for p in node_props.get(label, [])}
            for p in (props or []):
                # Last writer wins on types (live overrides declared
                # when both exist — schema-drift is real).
                current[p["name"]] = p
            node_props[label] = list(current.values())

    # 3. Relationship patterns — keyed by (src, rel, dst). Live count
    # takes precedence; declared entries with no live counterpart keep
    # `count = None` so the LLM knows the pattern is structurally
    # allowed but currently unpopulated.
    pat: dict[tuple[str, str, str], dict[str, Any]] = {}
    for p in (declared.get("relationship_patterns") or []):
        key = (p.get("src", ""), p.get("rel", ""), p.get("dst", ""))
        pat[key] = {**p, "declared": True}
    for p in (live.get("relationship_patterns") or []):
        key = (p.get("src", ""), p.get("rel", ""), p.get("dst", ""))
        if key in pat:
            pat[key] = {**pat[key], "count": p.get("count"), "observed": True}
        else:
            pat[key] = {**p, "observed": True}
    # Sorted: highest-observed-count first, then declared-only, then by name.
    def _pat_sort(p: dict[str, Any]):
        c = p.get("count")
        return (-(c if c is not None else -1), p.get("src", ""), p.get("rel", ""))
    rel_patterns = sorted(pat.values(), key = _pat_sort)

    # 4. Node samples — live wins (declared has none); preserve declared
    # for labels live didn't sample.
    node_samples: dict[str, list[dict[str, Any]]] = {}
    for source in (declared.get("node_samples") or {}, live.get("node_samples") or {}):
        for label, samples in source.items():
            if samples:
                node_samples[label] = samples
            elif label not in node_samples:
                node_samples[label] = []

    out: dict[str, Any] = {
        "labels":                labels,
        "relationship_types":    rels,
        "node_properties":       node_props,
        "relationship_patterns": rel_patterns,
        "node_samples":          node_samples,
    }
    if live.get("error"):
        out["error"] = live["error"]
    return out


async def get_neo4j_schema(*, request: Request, refresh: bool = False) -> dict[str, Any]:
    return await _schema_cached(
        backend = BACKEND_NEO4J, request = request, refresh = refresh,
        builder = _build_neo4j_schema,
    )


def _neo4j_value(v, *, Node, Rel, Path):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Node):
        return {
            "_kind":      "node",
            "id":         v.element_id,
            "labels":     list(v.labels),
            "properties": dict(v),
        }
    if isinstance(v, Rel):
        return {
            "_kind":      "relationship",
            "id":         v.element_id,
            "type":       v.type,
            "start":      v.start_node.element_id if v.start_node else None,
            "end":        v.end_node.element_id   if v.end_node   else None,
            "properties": dict(v),
        }
    if isinstance(v, Path):
        return {
            "_kind": "path",
            "nodes": [_neo4j_value(n, Node = Node, Rel = Rel, Path = Path) for n in v.nodes],
            "rels":  [_neo4j_value(r, Node = Node, Rel = Rel, Path = Path) for r in v.relationships],
        }
    if isinstance(v, (list, tuple)):
        return [_neo4j_value(x, Node = Node, Rel = Rel, Path = Path) for x in v]
    if isinstance(v, dict):
        return {k: _neo4j_value(x, Node = Node, Rel = Rel, Path = Path) for k, x in v.items()}
    return str(v)
