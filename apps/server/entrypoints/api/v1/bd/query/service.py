"""HTTP orchestration for the BD Query workbench."""
from __future__ import annotations
import domains
from . import schemas

import time

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse


def get_namespaces() -> dict:
    return domains.bd.query.source.service.namespaces()


async def run_raw_query(backend: schemas.QueryBackend, payload: schemas.RawQueryRequest) -> dict:
    return await domains.bd.query.source.service.run_raw_query(backend, payload.body)


async def get_schema(
    backend: schemas.QueryBackend,
) -> dict:
    schema = await domains.bd.query.source.service.query_schema(backend)
    return {
        "backend": backend,
        "app": "bd",
        "cached_at": int(time.time()),
        "schema": schema,
    }


async def list_history(
    request: Request,
    *,
    backend: schemas.QueryBackend | None,
    limit: int,
) -> dict:
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(status_code = 503, detail = "Postgres is not initialized.")
    items = await domains.bd.query.source.service.list_query_history(
        pg_url,
        backend = backend,
        limit = max(1, min(limit, 200)),
    )
    return {"items": items, "total": len(items)}


async def save_history(request: Request, payload: schemas.SaveQueryHistoryRequest) -> dict:
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(status_code = 503, detail = "Postgres is not initialized.")
    if not payload.body.strip():
        raise HTTPException(status_code = 400, detail = "`body` is required.")
    entry_id = await domains.bd.query.source.service.save_query_history(
        pg_url,
        backend = payload.backend,
        body = payload.body,
        prompt = payload.prompt,
    )
    return {"id": entry_id}


async def delete_history(request: Request, entry_id: int) -> dict:
    pg_url = getattr(request.app.state, "pg_url", None)
    if not pg_url:
        raise HTTPException(status_code = 503, detail = "Postgres is not initialized.")
    deleted = await domains.bd.query.source.service.delete_query_history(pg_url, entry_id)
    return {"deleted": deleted}


def generate_query(
    backend: schemas.QueryBackend,
    payload: schemas.QueryAIRequest,
    request: Request,
) -> StreamingResponse:
    if not payload.prompt.strip():
        raise HTTPException(status_code = 400, detail = "`prompt` is required.")
    model = (
        getattr(request.app.state, "query_ai_llm", None)
        or getattr(request.app.state, "llm", None)
    )

    async def event_source():
        async for frame in domains.bd.query.ai.service.generate_query_stream(
            model = model,
            backend = backend,
            user_prompt = payload.prompt,
            previous = payload.previous,
        ):
            yield f"data: {frame['data']}\n\n"

    return StreamingResponse(
        event_source(),
        media_type = "text/event-stream",
        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
