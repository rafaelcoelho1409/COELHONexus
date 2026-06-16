"""ycs/query — HTTP surface for the YCS Query workbench.

Mounted by `ycs/__init__.py` under `/api/v1/ycs/query`:

  GET  /query/namespaces            → support matrix (UI grey-out source)
  POST /query/elasticsearch         → free-text multi_match (legacy)
  POST /query/qdrant                → free-text kNN / scroll (legacy)
  POST /query/neo4j                 → free-text CONTAINS / browse (legacy)
  POST /query/raw/elasticsearch     → raw JSON body → _search (read-only)
  POST /query/raw/qdrant            → raw `{op, ...}` → AsyncQdrantClient
  POST /query/raw/neo4j             → raw Cypher in a READ-only tx
  GET  /query/schema/{backend}      → cached schema snapshot (Phase 3)
  POST /query/ai/{backend}          → NL → DSL SSE stream (Phase 4)
  GET|POST /query/history           → user query history (Phase 5)

Each backend endpoint returns 200 even on user-validation rejection
(`ok=False` envelope) so the editor can show the message inline
without juggling fetch status codes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from domains.ycs.query import (
    AIGenerateRequest,
    APP_BACKENDS,
    APPS,
    BACKENDS,
    NamespaceMap,
    QueryRequest,
    QueryResponse,
    RawQueryRequest,
    RawQueryResponse,
    query_es,
    query_neo4j,
    query_qdrant,
    raw_es,
    raw_neo4j,
    raw_qdrant,
)
from domains.ycs.query.schemas import NamespaceEntry


router = APIRouter()


@router.get("/namespaces", response_model = NamespaceMap)
async def get_namespaces() -> NamespaceMap:
    """Return the full (app x backend) support matrix. The Query page
    fetches this once on mount and uses it to grey out unsupported
    chips + render the "Searching in: …" caption."""
    matrix: dict[str, dict[str, NamespaceEntry]] = {}
    for app in APPS:
        matrix[app] = {}
        for backend in BACKENDS:
            ns = APP_BACKENDS[app][backend]
            matrix[app][backend] = NamespaceEntry(
                available = ns.available,
                label     = ns.label,
                target    = ns.target,
            )
    return NamespaceMap(
        apps     = list(APPS),
        backends = list(BACKENDS),
        matrix   = matrix,
    )


@router.post("/elasticsearch", response_model = QueryResponse)
async def post_query_es(
    payload: QueryRequest, request: Request,
) -> QueryResponse:
    return await query_es(
        app     = payload.app,
        q       = payload.q,
        limit   = payload.limit,
        offset  = payload.offset,
        request = request,
    )


@router.post("/qdrant", response_model = QueryResponse)
async def post_query_qdrant(
    payload: QueryRequest, request: Request,
) -> QueryResponse:
    return await query_qdrant(
        app     = payload.app,
        q       = payload.q,
        limit   = payload.limit,
        request = request,
    )


@router.post("/neo4j", response_model = QueryResponse)
async def post_query_neo4j(
    payload: QueryRequest, request: Request,
) -> QueryResponse:
    return await query_neo4j(
        app     = payload.app,
        q       = payload.q,
        limit   = payload.limit,
        request = request,
    )


# ====================================================================== #
# Raw DSL — Phase 1 SOTA workbench
# ====================================================================== #
@router.post("/raw/elasticsearch", response_model = RawQueryResponse)
async def post_raw_es(
    payload: RawQueryRequest, request: Request,
) -> RawQueryResponse:
    return await raw_es(
        app = payload.app, body_text = payload.body, request = request,
    )


@router.post("/raw/qdrant", response_model = RawQueryResponse)
async def post_raw_qdrant(
    payload: RawQueryRequest, request: Request,
) -> RawQueryResponse:
    return await raw_qdrant(
        app = payload.app, body_text = payload.body, request = request,
    )


@router.post("/raw/neo4j", response_model = RawQueryResponse)
async def post_raw_neo4j(
    payload: RawQueryRequest, request: Request,
) -> RawQueryResponse:
    return await raw_neo4j(
        app = payload.app, body_text = payload.body, request = request,
    )


# ====================================================================== #
# Schema discovery — Phase 3
# ====================================================================== #
@router.get("/schema/{backend}")
async def get_schema(
    backend: str, request: Request, refresh: bool = False,
) -> dict:
    """Return a cached snapshot of the backend's schema. `refresh=true`
    bypasses the Redis cache for one call."""
    from domains.ycs.query.service import (
        get_es_schema,
        get_neo4j_schema,
        get_qdrant_schema,
    )
    if backend == "elasticsearch":
        schema = await get_es_schema(request = request, refresh = refresh)
    elif backend == "qdrant":
        schema = await get_qdrant_schema(request = request, refresh = refresh)
    elif backend == "neo4j":
        schema = await get_neo4j_schema(request = request, refresh = refresh)
    else:
        raise HTTPException(status_code = 404, detail = f"unknown backend {backend!r}")
    # `cached_at` is set inside the cache wrapper; defend against the
    # rare path where the wrapper didn't populate it (e.g. cache hit on
    # an older blob that pre-dates the field).
    return {
        "backend":    backend,
        "app":        "ycs",
        "cached_at":  int(schema.get("cached_at") or 0),
        "schema":     {k: v for k, v in schema.items() if k != "cached_at"},
    }


# ====================================================================== #
# AI text-to-DSL SSE — Phase 4
# ====================================================================== #
_VALID_BACKENDS = {"elasticsearch", "qdrant", "neo4j"}


@router.post("/ai/{backend}")
async def post_ai_generate(
    backend: str, payload: AIGenerateRequest, request: Request,
) -> StreamingResponse:
    """Server-Sent Events stream of AI-generated DSL.

    Frames:
      `{"event": "start", "phase": "generate"}`
      `{"event": "chunk", "data": "..."}`        — streamed tokens
      `{"event": "repair", "error": "..."}`       — first attempt failed safety
      `{"event": "done", "ok": ..., "error": ..., "final": "..."}`

    The client (`query/ai.js`) replaces the editor with `final` on done
    so the user sees clean output even if the model produced a brief
    self-repair re-attempt mid-stream."""
    if backend not in _VALID_BACKENDS:
        raise HTTPException(
            status_code = 404, detail = f"unknown backend {backend!r}",
        )
    if not payload.prompt.strip():
        raise HTTPException(
            status_code = 400, detail = "`prompt` is required.",
        )
    from domains.ycs.query.service import ai_generate_stream

    async def event_source():
        try:
            async for frame in ai_generate_stream(
                backend     = backend,
                app         = payload.app,
                user_prompt = payload.prompt,
                previous    = payload.previous,
                request     = request,
            ):
                yield f"data: {frame['data']}\n\n"
        except Exception as e:
            import json as _json
            yield (
                "data: "
                + _json.dumps({"event": "error", "error": f"{type(e).__name__}: {e}"})
                + "\n\n"
            )

    return StreamingResponse(
        event_source(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control": "no-cache",
            "Connection":    "keep-alive",
        },
    )


# ====================================================================== #
# Query history — Phase 5 (Postgres-backed)
# ====================================================================== #
@router.get("/history")
async def list_history(
    request: Request,
    backend: str | None = None,
    limit:   int = 50,
) -> dict:
    """Return the latest history entries, newest-first. `backend` is an
    optional filter — UI passes the current backend so the user only
    sees relevant prior queries."""
    from domains.ycs.query.history import list_entries
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(
            status_code = 503,
            detail = "Postgres not initialized (YCS lifespan failed).",
        )
    items = await list_entries(pg_url, backend = backend, limit = max(1, min(limit, 200)))
    return {"items": items, "total": len(items)}


@router.post("/history")
async def save_history(
    request: Request,
) -> dict:
    """Persist one query into history. Body shape:
       `{backend, app?, body, prompt?, favorite?}`."""
    from domains.ycs.query.history import save_entry
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(
            status_code = 503,
            detail = "Postgres not initialized (YCS lifespan failed).",
        )
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code = 400, detail = "Invalid JSON body.")
    backend = payload.get("backend")
    body    = payload.get("body")
    if not backend or backend not in _VALID_BACKENDS:
        raise HTTPException(
            status_code = 400, detail = f"unknown or missing backend: {backend!r}",
        )
    if not body or not str(body).strip():
        raise HTTPException(status_code = 400, detail = "`body` is required.")
    entry_id = await save_entry(
        pg_url,
        backend  = backend,
        app      = payload.get("app", "ycs"),
        body     = str(body),
        prompt   = str(payload.get("prompt") or ""),
        favorite = bool(payload.get("favorite", False)),
    )
    return {"id": entry_id}


@router.delete("/history/{entry_id}")
async def delete_history(entry_id: int, request: Request) -> dict:
    from domains.ycs.query.history import delete_entry
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(
            status_code = 503,
            detail = "Postgres not initialized (YCS lifespan failed).",
        )
    n = await delete_entry(pg_url, entry_id)
    return {"deleted": n}
