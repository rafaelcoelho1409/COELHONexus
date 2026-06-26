"""ycs/query — Query workbench: namespaces, ES/Qdrant/Neo4j queries (simple + raw DSL), AI text-to-DSL SSE, history.
Backend endpoints return 200 even on validation rejection (`ok=False` envelope) for inline editor messages."""
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
    """Support matrix for the Query page — used to grey out unsupported chips."""
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


_VALID_BACKENDS = {"elasticsearch", "qdrant", "neo4j"}


@router.post("/ai/{backend}")
async def post_ai_generate(
    backend: str, payload: AIGenerateRequest, request: Request,
) -> StreamingResponse:
    """AI text-to-DSL SSE stream. `final` on `done` replaces the editor (clean output even after a self-repair mid-stream)."""
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
