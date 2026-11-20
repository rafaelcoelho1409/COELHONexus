"""HTTP orchestration for the YCS Query workbench."""
from __future__ import annotations

import time

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

import domains

from . import schemas


def get_namespaces() -> dict:
    return domains.ycs.query.source.service.namespaces()


async def run_simple_query(
    backend: schemas.BackendLiteral,
    payload: schemas.QueryRequest,
    request: Request,
) -> dict:
    return await domains.ycs.query.source.service.query_simple(
        backend,
        app = payload.app,
        query = payload.q,
        limit = payload.limit,
        offset = payload.offset,
        request = request,
    )


async def run_raw_query(
    backend: schemas.BackendLiteral,
    payload: schemas.RawQueryRequest,
    request: Request,
) -> dict:
    return await domains.ycs.query.source.service.run_raw_query(
        backend,
        app = payload.app,
        body_text = payload.body,
        request = request,
    )


async def get_schema(backend: schemas.BackendLiteral, request: Request, *, refresh: bool) -> dict:
    schema = await domains.ycs.query.source.service.query_schema(
        backend,
        request = request,
        refresh = refresh,
    )
    return {
        "backend": backend,
        "app": "ycs",
        "cached_at": int(schema.get("cached_at") or time.time()),
        "schema": {key: value for key, value in schema.items() if key != "cached_at"},
    }


def generate_query(
    backend: schemas.BackendLiteral,
    payload: schemas.AIGenerateRequest,
    request: Request,
) -> StreamingResponse:
    if not payload.prompt.strip():
        raise HTTPException(status_code = 400, detail = "`prompt` is required.")

    async def event_source():
        async for frame in domains.ycs.query.ai.service.generate_query_stream(
            backend = backend,
            user_prompt = payload.prompt,
            previous = payload.previous,
            request = request,
        ):
            yield f"data: {frame['data']}\n\n"

    return StreamingResponse(
        event_source(),
        media_type = "text/event-stream",
        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def list_history(
    request: Request,
    *,
    app: schemas.AppLiteral | None,
    backend: schemas.BackendLiteral | None,
    limit: int,
) -> dict:
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(status_code = 503, detail = "Postgres is not initialized.")
    items = await domains.ycs.query.source.service.list_query_history_entries(
        pg_url,
        app = app or "ycs",
        backend = backend,
        limit = max(1, min(limit, 200)),
    )
    return {"items": items, "total": len(items)}


async def save_history(request: Request, payload: schemas.SaveHistoryRequest) -> dict:
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(status_code = 503, detail = "Postgres is not initialized.")
    if not payload.body.strip():
        raise HTTPException(status_code = 400, detail = "`body` is required.")
    entry_id = await domains.ycs.query.source.service.save_query_history_entry(
        pg_url,
        backend = payload.backend,
        app = payload.app,
        body = payload.body,
        prompt = payload.prompt,
        favorite = payload.favorite,
    )
    return {"id": entry_id}


async def delete_history(request: Request, entry_id: int) -> dict:
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(status_code = 503, detail = "Postgres is not initialized.")
    deleted = await domains.ycs.query.source.service.delete_query_history_entry(
        pg_url,
        entry_id,
        app = "ycs",
    )
    return {"deleted": deleted}
