"""ycs/query — imperative shell. ES + Qdrant + Neo4j search per app.

Per docs/CODE-CONVENTIONS.md §4: I/O orchestration only. Pure projection
delegated to `domain.py`; identifier strings + the (app, backend) map
delegated to `params.py`; HTTP boundary validation to `schemas.py`.

All three entry points (`query_es`, `query_qdrant`, `query_neo4j`)
share the same shape:
    in:  app, q, limit, offset, request
    out: schemas.QueryResponse (`supported=False` short-circuit if the app
         has no presence in that backend).

The functions are best-effort: a store hiccup degrades to
`error=...` on the response (HTTP 200 with the error field set)
rather than 5xx, so the Query page can render one failed tab without
killing the other two."""
from __future__ import annotations

import logging
import time
from typing import Any

import domains
import psycopg
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

from . import domain, errors, params, prompts, schemas


logger = logging.getLogger(__name__)


def _unsupported(backend: str, app: str, q: str) -> schemas.QueryResponse:
    """Same shape as a real response — `supported=False` is the only
    signal the UI needs to render the "no data here" state."""
    return schemas.QueryResponse(
        **domain.unsupported_response(backend, app, q),  # type: ignore[arg-type]
    )


def _envelope(
    backend: str, app: str, q: str, hits: list[dict[str, Any]],
    total: int, t0: float, error: str | None = None,
) -> schemas.QueryResponse:
    return schemas.QueryResponse(
        backend   = backend,           # type: ignore[arg-type]
        app       = app,               # type: ignore[arg-type]
        supported = True,
        namespace = params.namespace_label(app, backend),
        q         = q,
        total     = total,
        took_ms   = int((time.monotonic() - t0) * 1000),
        hits      = [schemas.QueryHit(**h) for h in hits],
        error     = error,
    )


async def query_es(
    *, app: str, q: str, limit: int, offset: int, request: Request,
) -> schemas.QueryResponse:
    """Multi-index ES search for the selected app.

    YCS path: multi_match across both the metadata index (title +
    description + channel) AND the transcriptions index (content). The
    projector branches on `_index` to render the right hit shape.

    Empty `q` returns a `match_all` page so the user can browse without
    typing — same idiom as the Ingest library view."""
    if not params.is_supported(app, params.BACKEND_ES):
        return _unsupported(params.BACKEND_ES, app, q)

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
            params.BACKEND_ES, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )

    raw_hits = response.get("hits", {}).get("hits", [])
    total    = int(response.get("hits", {}).get("total", {}).get("value", 0))
    hits     = [domain.project_es_hit(h, app = app) for h in raw_hits]
    return _envelope(params.BACKEND_ES, app, q, hits, total, t0)


async def query_qdrant(
    *, app: str, q: str, limit: int, request: Request,
) -> schemas.QueryResponse:
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
    if not params.is_supported(app, params.BACKEND_QDRANT):
        return _unsupported(params.BACKEND_QDRANT, app, q)

    collection = params.APP_BACKENDS[app][params.BACKEND_QDRANT].target

    client = get_qdrant()
    t0 = time.monotonic()
    raw_q = q.strip()

    if raw_q:
        smart = getattr(request.app.state, "smart_retriever", None)
        embedder = getattr(getattr(smart, "qdrant_retriever", None), "dense_embeddings", None)
        if embedder is None:
            return _envelope(
                params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = "dense embedder not initialized (YCS lifespan failed?)",
            )
        try:
            vector = embedder.embed_query(raw_q)
        except Exception as e:
            logger.warning(f"[ycs:query:qdrant] embed failed: {type(e).__name__}: {e}")
            return _envelope(
                params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = f"embed failed: {type(e).__name__}: {str(e)[:160]}",
            )
        # YCS uses NAMED vectors ("dense"/"sparse"); RR uses the default unnamed vector.
        using = "dense" if app == params.APP_YCS else None
        try:
            # 2026-09-17: `AsyncQdrantClient.search()` was removed in
            # qdrant-client 1.16 (live-confirmed: `AttributeError:
            # 'AsyncQdrantClient' object has no attribute 'search'` —
            # this call site had silently been dead since whatever
            # upgrade dropped it). `query_points()` is the replacement:
            # the named-vector selector moves from a `(name, vector)`
            # tuple into its own `using=` kwarg, and the vector itself
            # goes bare into `query=`. Same shape `retriever/service.py`'s
            # `QdrantHybridRetriever` already uses for the RAG path.
            response = await client.query_points(
                collection_name = collection,
                query           = vector,
                using           = using,
                limit           = limit,
                with_payload    = True,
            )
            results = response.points
        except Exception as e:
            logger.warning(f"[ycs:query:qdrant] search failed: {type(e).__name__}: {e}")
            return _envelope(
                params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
                error = f"{type(e).__name__}: {str(e)[:200]}",
            )
        hits = [domain.project_qdrant_point(p, app = app) for p in results]
        return _envelope(params.BACKEND_QDRANT, app, q, hits, total = len(hits), t0 = t0)

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
            params.BACKEND_QDRANT, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )
    hits = [domain.project_qdrant_point(r, app = app) for r in records]
    return _envelope(params.BACKEND_QDRANT, app, q, hits, total = len(hits), t0 = t0)


# CONTAINS over toLower vs fulltext index: zero bootstrap cost; fast enough for current corpus size.
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
) -> schemas.QueryResponse:
    """Cypher search over the app's labels.

    Two parameter shapes: `{needle, limit}` for `_*_SEARCH` (CONTAINS
    over title/name/id/text), `{limit}` for `_*_BROWSE` (no filter).
    `needle` is pre-lowered so the Cypher only does `toLower(field)
    CONTAINS $needle` once per field — cheaper than `=~ "(?i)..."`."""
    if not params.is_supported(app, params.BACKEND_NEO4J):
        return _unsupported(params.BACKEND_NEO4J, app, q)

    raw_q = q.strip()
    if app == params.APP_RR:
        cypher = _RR_CYPHER_SEARCH if raw_q else _RR_CYPHER_BROWSE
    else:
        cypher = _YCS_CYPHER_SEARCH if raw_q else _YCS_CYPHER_BROWSE

    cypher_params: dict[str, Any] = {"limit": limit}
    if raw_q:
        cypher_params["needle"] = raw_q.lower()

    t0 = time.monotonic()
    try:
        driver = get_driver()
        async with driver.session(database = NEO4J_DATABASE) as session:
            result = await session.run(cypher, cypher_params)
            records = [dict(record) async for record in result]
    except Exception as e:
        logger.warning(f"[ycs:query:neo4j] cypher failed: {type(e).__name__}: {e}")
        return _envelope(
            params.BACKEND_NEO4J, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )

    hits = [domain.project_neo4j_row(r, app = app) for r in records]
    return _envelope(params.BACKEND_NEO4J, app, q, hits, total = len(hits), t0 = t0)


# Raw DSL — Phase 1 of the SOTA workbench. User-supplied DSL/Cypher/JSON.
def _raw_envelope(
    backend: str, app: str, t0: float,
    *,
    hits:  list[dict[str, Any]] | None = None,
    total: int | None = None,
    notes: list[str] | None = None,
    error: str | None = None,
    ok:    bool = True,
) -> schemas.RawQueryResponse:
    return schemas.RawQueryResponse(
        backend = backend,                  # type: ignore[arg-type]
        app     = app,                      # type: ignore[arg-type]
        ok      = ok,
        error   = error,
        notes   = list(notes or []),
        took_ms = int((time.monotonic() - t0) * 1000),
        total   = total,
        hits    = [schemas.RawQueryHit(**h) for h in (hits or [])],
    )


def _raw_disallowed(backend: str, app: str, msg: str) -> schemas.RawQueryResponse:
    """Validation rejection — returned with `ok=False` so the editor's
    error band lights up. HTTP layer still returns 200 to keep the
    SSE / fetch flow simple."""
    return schemas.RawQueryResponse(**domain.raw_disallowed_response(backend, app, msg))


# Elasticsearch — POST the validated body straight at `_search` on the
# YCS metadata + transcriptions indexes. The URL path is server-pinned
# (the user never sees /controls it) so the only attack surface is the
# JSON body — and `parse_es_body` shaped it.
async def raw_es(
    *, app: str, body_text: str, request: Request,
) -> schemas.RawQueryResponse:
    if not params.is_supported(app, params.BACKEND_ES):
        return _raw_disallowed(
            params.BACKEND_ES, app,
            f"{app!r} has no presence in Elasticsearch.",
        )
    try:
        parsed = domain.parse_es_body(body_text)
    except errors.QueryNotAllowed as e:
        return _raw_disallowed(params.BACKEND_ES, app, str(e))

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


# Qdrant — the editor body is `{"op": ..., ...}`. Dispatch off `op`,
# pin the collection name from the (app, backend) matrix so the user
# can't query a different collection.
async def raw_qdrant(
    *, app: str, body_text: str, request: Request,
) -> schemas.RawQueryResponse:
    if not params.is_supported(app, params.BACKEND_QDRANT):
        return _raw_disallowed(
            params.BACKEND_QDRANT, app,
            f"{app!r} has no presence in Qdrant.",
        )
    try:
        parsed = domain.parse_qdrant_body(body_text)
    except errors.QueryNotAllowed as e:
        return _raw_disallowed(params.BACKEND_QDRANT, app, str(e))

    collection = params.APP_BACKENDS[app][params.BACKEND_QDRANT].target

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
            # 2026-09-17: `AsyncQdrantClient.search()` was removed in
            # qdrant-client 1.16 (live-confirmed via the semantic-search
            # tab throwing `AttributeError: 'AsyncQdrantClient' object
            # has no attribute 'search'`) — "search" is this editor's
            # DEFAULT op (`domain.py::parse_qdrant_body`), so this was
            # broken for anyone who didn't explicitly type `"op":
            # "query_points"`. Translate the legacy body shape and
            # dispatch through `query_points()` instead, same as the
            # explicit `query_points` branch below.
            r = await client.query_points(**domain.translate_search_body(body))
            results = getattr(r, "points", r)
        elif op == "scroll":
            records, _ = await client.scroll(**body)
            results    = records
        elif op == "query_points":
            r = await client.query_points(**body)
            results = getattr(r, "points", r)
        elif op == "count":
            n = await client.count(**body)
            return _raw_envelope(
                params.BACKEND_QDRANT, app, t0,
                hits  = [{"summary": f"count={n.count}", "raw": {"count": n.count}}],
                total = int(n.count),
                notes = notes,
            )
        else:
            return _raw_disallowed(params.BACKEND_QDRANT, app, f"unknown op {op!r}")
    except TypeError as e:
        # Pydantic / qdrant-client kwargs mismatch on user-supplied body.
        return _raw_envelope(
            params.BACKEND_QDRANT, app, t0, ok = False,
            error = f"Invalid Qdrant body for op={op!r}: {e}",
            notes = notes,
        )
    except Exception as e:
        logger.warning(f"[ycs:query:raw_qdrant] failed: {type(e).__name__}: {e}")
        return _raw_envelope(
            params.BACKEND_QDRANT, app, t0, ok = False,
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
        params.BACKEND_QDRANT, app, t0,
        hits  = projected,
        total = len(projected),
        notes = notes,
    )


# Neo4j — run user Cypher inside a read-only transaction. Driver
# distinguishes `session.execute_read(...)` from `.execute_write(...)`;
# we ALWAYS use the read variant so even a write-keyword that slipped
# past the regex (a future Cypher addition we didn't anticipate) is
# rejected by the server itself.
async def raw_neo4j(
    *, app: str, body_text: str, request: Request,
) -> schemas.RawQueryResponse:
    if not params.is_supported(app, params.BACKEND_NEO4J):
        return _raw_disallowed(
            params.BACKEND_NEO4J, app,
            f"{app!r} has no presence in Neo4j.",
        )
    try:
        domain.assert_cypher_readonly(body_text)
    except errors.QueryNotAllowed as e:
        return _raw_disallowed(params.BACKEND_NEO4J, app, str(e))

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
            params.BACKEND_NEO4J, app, t0, ok = False,
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
            "raw":     domain.neo4j_jsonify(r),
        })
    return _raw_envelope(
        params.BACKEND_NEO4J, app, t0,
        hits  = projected,
        total = len(projected),
    )


# AI text-to-DSL — Phase 4
# 2026-09-17: the model chip used to come from a `_ModelCapture`
# `AsyncCallbackHandler` reading `chunk.response_metadata` off
# `llm.astream()` — live-verified (direct probe against the real
# rotator, several attempts) that NEITHER the per-chunk
# `response_metadata` NOR the final aggregated streaming message ever
# carries the real resolved deployment when the Settings-page model is
# `"auto"` — every chunk just echoes back the literal request value
# `"auto"`, so the chip could never move past that placeholder no
# matter how the callback tried to read it. A plain non-streaming
# `llm.ainvoke()` call on the SAME client DOES correctly return it in
# `response.response_metadata["model_name"]` — that's exactly the field
# `domains.ycs.rag.service.capture_llm_usage`/`resilient_ainvoke`
# already read for DD's Planner/Synth and YCS Ingestion/Ask's model
# chips and usage tables. Rather than fight the rotator's streaming
# response shape, `_stream()` below now generates the SAME way those
# do — one `ainvoke()`, not a token stream — and reports whatever chunk
# size it gets back as a single SSE "chunk" event, trading live token-
# by-token typing (NL→DSL outputs are short JSON/Cypher, not prose)
# for a model chip that's actually correct.


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
      3. Generate via one `ainvoke()` call (see `_stream`'s docstring
         for why not a token stream) and forward the full text to the
         client as a single `data: {"chunk": "..."}` frame.
      4. After the stream completes, run the same safety guard the Run
         path uses. On rejection, ONE self-repair retry (full re-generate
         with the error fed in).
      5. Emit a terminal `data: {"event": "done", "ok": ..., "final":
         "..."}` frame so the client can replace the editor with the
         clean text (vs. the streamed-with-self-repair chatter)."""
    import json as _json

    # prefer the speed-optimised `dd-reduce-label` chain
    # built once at lifespan as `app.state.query_ai_llm`. It targets
    # fast non-reasoning arms (Groq Llama-3.3-70b LPU, Gemini Flash
    # Lite, NIM gpt-oss-120b, …) instead of `dd-all`'s reasoning-heavy
    # 1-15 s of `<think>` tokens before any DSL). For NL → DSL, which
    # is deterministic structural translation, reasoning is wasted
    # `app.state.llm` is the graceful fallback when the fast chain
    # failed to init (Settings-page endpoint unreachable at lifespan,
    # a race, etc.) — still functional, just slower.
    llm = (
        getattr(request.app.state, "query_ai_llm", None)
        or getattr(request.app.state, "llm", None)
    )
    if llm is None:
        yield {"data": _json.dumps({
            "event": "error",
            "error": "LLM endpoint not initialized (YCS lifespan failed).",
        })}
        return

    # 1. Schema. Best-effort — degrades to None on any failure.
    schema_payload = None
    try:
        if backend == params.BACKEND_ES:
            schema_payload = await get_es_schema(request = request)
        elif backend == params.BACKEND_QDRANT:
            schema_payload = await get_qdrant_schema(request = request)
        elif backend == params.BACKEND_NEO4J:
            schema_payload = await get_neo4j_schema(request = request)
    except Exception as e:
        logger.warning(f"[ycs:query:ai] schema fetch failed: {type(e).__name__}: {e}")

    schema_obj = None
    if isinstance(schema_payload, dict):
        # The cached-response wrapper adds `cached_at` at the top level;
        # the prompt builders want the raw schema dict. Strip the
        # wrapper key.
        schema_obj = {k: v for k, v in schema_payload.items() if k != "cached_at"}

    examples = prompts.EXAMPLES_BY_BACKEND.get(backend, [])

    async def _stream(prompt_text: str):
        """Yield `(kind, payload, accumulated)` tuples — `kind` is one of
        {`yield`, `model`, `done`, `error`}. We never `return value`-out
        of this async generator (Python forbids it); the accumulated
        text is threaded through the tuple so the caller has it on the
        terminal `done` frame.

        One `ainvoke()`, not a token stream — see the module comment
        above `ai_generate_stream` for why: `response.response_metadata`
        is the only place the real resolved deployment reliably shows
        up for this rotator, and that field is only populated on the
        non-streaming response shape."""
        accumulated = ""
        try:
            response = await llm.ainvoke(prompt_text)
        except Exception as e:
            yield ("error", f"{type(e).__name__}: {e}", accumulated)
            return

        meta = getattr(response, "response_metadata", None) or {}
        model = meta.get("model_name") or meta.get("model")
        if model:
            yield ("model", model, accumulated)

        text = getattr(response, "content", "") or ""
        if isinstance(text, list):
            text = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in text
            )
        text = str(text)
        if text:
            accumulated = text
            payload = _json.dumps({"event": "chunk", "data": text})
            yield ("yield", payload, accumulated)

        try:
            await domains.ycs.rag.service.capture_llm_usage(response)
        except Exception:
            pass

        yield ("done", "", accumulated)

    async def _stream_with_retry(prompt_text: str, *, max_attempts: int = 2):
        """One retry when an attempt fails before emitting any real
        text — every OTHER LLM call site in this codebase goes through
        `resilient_ainvoke` (max_attempts=2); this generator-streaming
        path never had an equivalent, so a single bad FGTS-VA bandit
        pick was a dead end straight to the user.

        2026-09-17: live-reproduced on the Qdrant backend — the bandit
        picked NVIDIA NIM (`gpt-oss-20b`, then `z-ai/glm-5.3` on a
        second attempt) and both hung for the full 120s client timeout
        with zero tokens back, while Gemini sat benched on an already-
        exhausted daily free-tier cap. Only retries when NOTHING has
        streamed yet (`kind == "yield"` never seen) — a `model` event
        alone doesn't count, since the rotator emits that from
        `on_chat_model_start` before the provider has sent anything.
        Once real text has reached the client, a later failure ships
        as-is rather than risk a duplicated/confusing second
        generation on top of what's already rendered."""
        for attempt in range(max_attempts):
            got_text = False
            async for kind, payload, txt in _stream(prompt_text):
                if kind == "yield":
                    got_text = True
                if (
                    kind == "error" and not got_text
                    and attempt + 1 < max_attempts
                ):
                    logger.warning(
                        f"[ycs:query:ai] stream attempt {attempt + 1}/"
                        f"{max_attempts} failed before any text "
                        f"({payload!r}); retrying"
                    )
                    break
                yield (kind, payload, txt)
                if kind in ("done", "error"):
                    return

    # First pass.
    prompt1 = prompts.build_generate_prompt(
        backend     = backend,
        user_prompt = user_prompt,
        schema      = schema_obj,
        examples    = examples,
        previous    = previous,
    )
    yield {"data": _json.dumps({"event": "start", "phase": "generate"})}
    acc = ""
    stream_err: str | None = None
    async for kind, payload, txt in _stream_with_retry(prompt1):
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

    final = domain.post_clean(acc, backend = backend)
    ok, err = domain.check_with_safety(final, backend = backend)
    if not ok:
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
        prompt2 = prompts.build_repair_prompt(
            backend     = backend,
            user_prompt = user_prompt,
            attempt     = final,
            error       = err or "",
            schema      = schema_obj,
            examples    = examples,
        )
        acc2 = ""
        async for kind, payload, txt in _stream_with_retry(prompt2):
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
                "error": stream_err, "final": domain.post_clean(acc2, backend = backend),
            })}
            return
        final = domain.post_clean(acc2, backend = backend)
        ok, err = domain.check_with_safety(final, backend = backend)
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


# Two-layer schema: declared floor (structural contract) + live overlay (real samples + LLM-generated rel names).
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
                    "_source": domain.truncate_doc(h.get("_source", {}) or {}),
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


async def _build_es_schema() -> dict[str, Any]:
    """Two-layer ES schema: declared floor + live overlay.

    Declared floor (`domain.declared_es_schema`) is sourced from
    `infra/elasticsearch/mappings.py` so an empty cluster / outage
    still surfaces the full mapping. Live overlay merges per-index
    samples + field_values + observed mappings (in case ES has drifted
    from what we declared) + doc_count."""
    declared = domain.declared_es_schema()
    try:
        live = await _build_es_schema_live()
    except Exception as e:
        logger.warning(f"[ycs:query:schema:es] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return domain.merge_es_schema(declared, live)


async def get_es_schema(*, request: Request, refresh: bool = False) -> dict[str, Any]:
    return await _schema_cached(
        backend = params.BACKEND_ES, request = request, refresh = refresh,
        builder = _build_es_schema,
    )


async def _build_qdrant_schema_live() -> dict[str, Any]:
    """Qdrant live schema — overlay for the two-layer merge.

    Returns the per-collection vectors_config + declared payload_schema
    + observed_payload_keys (union of keys across sampled payloads) +
    3 sample payloads. Degrades to empty when the collection is empty;
    `_build_qdrant_schema` merges this on top of the declared floor."""
    collection = params.APP_BACKENDS[params.APP_YCS][params.BACKEND_QDRANT].target
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

    # Sample payloads: payload_schema only has indexed keys; scrolling catches additional unindexed ones.
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
                "payload": domain.truncate_doc(payload),
            })
    except Exception as e:
        logger.debug(f"[ycs:query:schema:qdrant] sample scroll failed: {e}")

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

    Declared floor (`domain.declared_qdrant_schema`) lists the FULL
    canonical payload keys from `domains.ycs.ingestion.domain
    .build_payload`. Live overlay adds the actual points_count + real
    sample payloads (so the LLM sees concrete values, not just keys)."""
    declared = domain.declared_qdrant_schema()
    try:
        live = await _build_qdrant_schema_live()
    except Exception as e:
        logger.warning(f"[ycs:query:schema:qdrant] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return domain.merge_qdrant_schema(declared, live)


async def get_qdrant_schema(*, request: Request, refresh: bool = False) -> dict[str, Any]:
    return await _schema_cached(
        backend = params.BACKEND_QDRANT, request = request, refresh = refresh,
        builder = _build_qdrant_schema,
    )


# All read procedures only — no APOC dep.
_SCHEMA_CYPHER_LABELS = "CALL db.labels() YIELD label RETURN collect(label) AS labels"
_SCHEMA_CYPHER_RELS   = "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS rels"
_SCHEMA_CYPHER_PROPS  = (
    "CALL db.schema.nodeTypeProperties() "
    "YIELD nodeLabels, propertyName, propertyTypes "
    "RETURN nodeLabels, propertyName, propertyTypes "
    "ORDER BY nodeLabels, propertyName"
)
# db.schema.visualization() would be cheaper but is APOC-only.
_SCHEMA_CYPHER_REL_PATTERNS = """
MATCH (a)-[r]->(b)
WITH labels(a)[0] AS src, type(r) AS rel, labels(b)[0] AS dst, count(*) AS n
WHERE src IS NOT NULL AND dst IS NOT NULL
RETURN src, rel, dst, n
ORDER BY n DESC
LIMIT 50
"""
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
                            "properties": domain.truncate_doc(props),
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

    Declared floor (`domain.declared_neo4j_schema`) is sourced from
    the actual writer code in `domains/ycs/graph_builder/` so it
    matches what the LLMGraphTransformer + the YCS-specific
    `build_video_metadata_graph` produce."""
    declared = domain.declared_neo4j_schema()
    try:
        live = await _build_neo4j_schema_live()
    except Exception as e:
        logger.warning(f"[ycs:query:schema:neo4j] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return domain.merge_neo4j_schema(declared, live)


async def get_neo4j_schema(*, request: Request, refresh: bool = False) -> dict[str, Any]:
    return await _schema_cached(
        backend = params.BACKEND_NEO4J, request = request, refresh = refresh,
        builder = _build_neo4j_schema,
    )


# Per-user query history (Postgres-backed).
#
# Tiny table (one schema, three queries). Uses `psycopg` v3 — same driver
# the YCS conversation service uses, the only async-Postgres lib actually
# present in the FastAPI image (`asyncpg` would have to be added to
# `pyproject.toml` + a wheel rebuild; staying on psycopg keeps the
# deploy surface unchanged).
#
# There's no auth yet — every user sees every row. Add an `owner` column
# + filter when SSO lands; the schema below already accommodates it as a
# nullable text.

_HISTORY_TABLE_NAME = "query_history"


async def ensure_query_history_table(pg_url: str) -> None:
    """Idempotent table init. Called lazily on first read/write — keeps
    Query out of the lifespan hot path (cheap when already created)."""
    async with await psycopg.AsyncConnection.connect(
        pg_url, autocommit = True,
    ) as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_HISTORY_TABLE_NAME} (
                id           BIGSERIAL PRIMARY KEY,
                backend      TEXT      NOT NULL,
                app          TEXT      NOT NULL DEFAULT 'ycs',
                body         TEXT      NOT NULL,
                prompt       TEXT      NOT NULL DEFAULT '',
                favorite     BOOLEAN   NOT NULL DEFAULT FALSE,
                owner        TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS query_history_created_idx
                ON {_HISTORY_TABLE_NAME} (created_at DESC)
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS query_history_backend_idx
                ON {_HISTORY_TABLE_NAME} (backend, created_at DESC)
        """)


async def save_query_history_entry(
    pg_url: str, *, backend: str, app: str, body: str, prompt: str,
    favorite: bool = False,
) -> int:
    """Insert one row, return its id. Errors propagate up so the router
    can 5xx on Postgres outages instead of silently no-op'ing."""
    await ensure_query_history_table(pg_url)
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"INSERT INTO {_HISTORY_TABLE_NAME} "
            f"(backend, app, body, prompt, favorite) "
            f"VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (backend, app, body, prompt, favorite),
        )
        row = await result.fetchone()
        await conn.commit()
    return int(row[0]) if row else 0


async def list_query_history_entries(
    pg_url: str, *, backend: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the latest `limit` entries, optionally filtered to one
    backend. Body is included so the UI can show a snippet without an
    extra round-trip."""
    await ensure_query_history_table(pg_url)
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        if backend:
            result = await conn.execute(
                f"SELECT id, backend, app, body, prompt, favorite, created_at "
                f"FROM {_HISTORY_TABLE_NAME} WHERE backend = %s "
                f"ORDER BY created_at DESC LIMIT %s",
                (backend, limit),
            )
        else:
            result = await conn.execute(
                f"SELECT id, backend, app, body, prompt, favorite, created_at "
                f"FROM {_HISTORY_TABLE_NAME} "
                f"ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        rows = await result.fetchall()
    return [
        {
            "id":         int(r[0]),
            "backend":    r[1],
            "app":        r[2],
            "body":       r[3],
            "prompt":     r[4] or "",
            "favorite":   bool(r[5]),
            "created_at": r[6].isoformat() if r[6] is not None else "",
        }
        for r in rows
    ]


async def delete_query_history_entry(pg_url: str, entry_id: int) -> int:
    """DELETE one row by id. Returns 1 if removed, 0 if not found."""
    await ensure_query_history_table(pg_url)
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"DELETE FROM {_HISTORY_TABLE_NAME} WHERE id = %s",
            (entry_id,),
        )
        await conn.commit()
    # psycopg cursor.rowcount carries the affected-row count.
    return int(getattr(result, "rowcount", 0) or 0)
