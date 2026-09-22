"""ycs/query — Query workbench: namespaces, ES/Qdrant/Neo4j queries (simple + raw DSL), AI text-to-DSL SSE, history.
Backend endpoints return 200 even on validation rejection (`ok=False` envelope) for inline editor messages."""
from __future__ import annotations

import domains

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse


router = APIRouter()


@router.get("/namespaces", response_model = domains.ycs.query.schemas.NamespaceMap)
async def get_namespaces() -> domains.ycs.query.schemas.NamespaceMap:
    """Support matrix for the Query page — used to grey out unsupported chips."""
    matrix: dict[str, dict[str, domains.ycs.query.schemas.NamespaceEntry]] = {}
    for app in domains.ycs.query.params.APPS:
        matrix[app] = {}
        for backend in domains.ycs.query.params.BACKENDS:
            ns = domains.ycs.query.params.APP_BACKENDS[app][backend]
            matrix[app][backend] = domains.ycs.query.schemas.NamespaceEntry(
                available = ns.available,
                label     = ns.label,
                target    = ns.target,
            )
    return domains.ycs.query.schemas.NamespaceMap(
        apps     = list(domains.ycs.query.params.APPS),
        backends = list(domains.ycs.query.params.BACKENDS),
        matrix   = matrix,
    )


@router.post("/elasticsearch", response_model = domains.ycs.query.schemas.QueryResponse)
async def post_query_es(
    payload: domains.ycs.query.schemas.QueryRequest, request: Request,
) -> domains.ycs.query.schemas.QueryResponse:
    return await domains.ycs.query.service.query_es(
        app     = payload.app,
        q       = payload.q,
        limit   = payload.limit,
        offset  = payload.offset,
        request = request,
    )


@router.post("/qdrant", response_model = domains.ycs.query.schemas.QueryResponse)
async def post_query_qdrant(
    payload: domains.ycs.query.schemas.QueryRequest, request: Request,
) -> domains.ycs.query.schemas.QueryResponse:
    return await domains.ycs.query.service.query_qdrant(
        app     = payload.app,
        q       = payload.q,
        limit   = payload.limit,
        request = request,
    )


@router.post("/neo4j", response_model = domains.ycs.query.schemas.QueryResponse)
async def post_query_neo4j(
    payload: domains.ycs.query.schemas.QueryRequest, request: Request,
) -> domains.ycs.query.schemas.QueryResponse:
    return await domains.ycs.query.service.query_neo4j(
        app     = payload.app,
        q       = payload.q,
        limit   = payload.limit,
        request = request,
    )


@router.post("/raw/elasticsearch", response_model = domains.ycs.query.schemas.RawQueryResponse)
async def post_raw_es(
    payload: domains.ycs.query.schemas.RawQueryRequest, request: Request,
) -> domains.ycs.query.schemas.RawQueryResponse:
    return await domains.ycs.query.service.raw_es(
        app = payload.app, body_text = payload.body, request = request,
    )


@router.post("/raw/qdrant", response_model = domains.ycs.query.schemas.RawQueryResponse)
async def post_raw_qdrant(
    payload: domains.ycs.query.schemas.RawQueryRequest, request: Request,
) -> domains.ycs.query.schemas.RawQueryResponse:
    return await domains.ycs.query.service.raw_qdrant(
        app = payload.app, body_text = payload.body, request = request,
    )


@router.post("/raw/neo4j", response_model = domains.ycs.query.schemas.RawQueryResponse)
async def post_raw_neo4j(
    payload: domains.ycs.query.schemas.RawQueryRequest, request: Request,
) -> domains.ycs.query.schemas.RawQueryResponse:
    return await domains.ycs.query.service.raw_neo4j(
        app = payload.app, body_text = payload.body, request = request,
    )


@router.get("/schema/{backend}")
async def get_schema(
    backend: str, request: Request, refresh: bool = False,
) -> dict:
    """Return a cached snapshot of the backend's schema. `refresh=true`
    bypasses the Redis cache for one call."""
    if backend == "elasticsearch":
        schema = await domains.ycs.query.service.get_es_schema(request = request, refresh = refresh)
    elif backend == "qdrant":
        schema = await domains.ycs.query.service.get_qdrant_schema(request = request, refresh = refresh)
    elif backend == "neo4j":
        schema = await domains.ycs.query.service.get_neo4j_schema(request = request, refresh = refresh)
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


@router.post("/ai/{backend}")
async def post_ai_generate(
    backend: str, payload: domains.ycs.query.schemas.AIGenerateRequest, request: Request,
) -> StreamingResponse:
    """AI text-to-DSL SSE stream. `final` on `done` replaces the editor (clean output even after a self-repair mid-stream)."""
    if backend not in domains.ycs.query.params.BACKENDS:
        raise HTTPException(
            status_code = 404, detail = f"unknown backend {backend!r}",
        )
    if not payload.prompt.strip():
        raise HTTPException(
            status_code = 400, detail = "`prompt` is required.",
        )

    async def event_source():
        try:
            async for frame in domains.ycs.query.service.ai_generate_stream(
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
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(
            status_code = 503,
            detail = "Postgres not initialized (YCS lifespan failed).",
        )
    items = await domains.ycs.query.service.list_query_history_entries(pg_url, backend = backend, limit = max(1, min(limit, 200)))
    return {"items": items, "total": len(items)}


@router.post("/history")
async def save_history(
    request: Request,
) -> dict:
    """Persist one query into history. Body shape:
       `{backend, app?, body, prompt?, favorite?}`."""
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
    if not backend or backend not in domains.ycs.query.params.BACKENDS:
        raise HTTPException(
            status_code = 400, detail = f"unknown or missing backend: {backend!r}",
        )
    if not body or not str(body).strip():
        raise HTTPException(status_code = 400, detail = "`body` is required.")
    entry_id = await domains.ycs.query.service.save_query_history_entry(
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
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(
            status_code = 503,
            detail = "Postgres not initialized (YCS lifespan failed).",
        )
    n = await domains.ycs.query.service.delete_query_history_entry(pg_url, entry_id)
    return {"deleted": n}
