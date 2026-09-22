"""External endpoint settings — the two OpenAI-compatible URLs Nexus calls.

Raw keys travel browser→FastAPI once on save; responses carry only
masked status (`has_key`/`source`/`last4`). Every mutation resets the
corresponding pooled client so the change propagates to the next call.
"""
from __future__ import annotations
import domains
from . import schemas, service

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/endpoint")
async def get_endpoint() -> JSONResponse:
    return JSONResponse(content=await run_in_threadpool(service._endpoint_view))


@router.put("/endpoint")
async def put_endpoint(body: schemas.EndpointBody) -> JSONResponse:
    try:
        await run_in_threadpool(service._write_endpoint, body)
    except domains.settings.credentials.errors.UnmanagedKeyEnv as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content=await run_in_threadpool(service._endpoint_view))


@router.post("/endpoint/test")
async def test_endpoint() -> JSONResponse:
    """One tiny completion against the currently-configured endpoint."""
    import time as _time

    t0 = _time.monotonic()
    try:
        text, meta = await domains.settings.chat.service.chat_text_async(
            "Reply with exactly: OK", max_tokens=5, timeout_s=20.0,
        )
        return JSONResponse(content={
            "ok": True,
            "reply": (text or "").strip()[:80],
            "latency_ms": int((_time.monotonic() - t0) * 1000),
            "model": (meta or {}).get("model"),
        })
    except Exception as e:
        return JSONResponse(content={
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "latency_ms": int((_time.monotonic() - t0) * 1000),
        })


@router.get("/embedding")
async def get_embedding() -> JSONResponse:
    return JSONResponse(content=await run_in_threadpool(service._embedding_view))


@router.put("/embedding")
async def put_embedding(body: schemas.EmbeddingBody) -> JSONResponse:
    try:
        await run_in_threadpool(service._write_embedding, body)
    except domains.settings.credentials.errors.UnmanagedKeyEnv as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content=await run_in_threadpool(service._embedding_view))


@router.post("/embedding/test")
async def test_embedding() -> JSONResponse:
    """One tiny real embeddings call against the currently-configured
    embedding endpoint — proves it actually works."""
    import time as _time

    t0 = _time.monotonic()
    try:
        vector, meta = await domains.settings.embeddings.service.embed_probe_async()
        return JSONResponse(content={
            "ok": True,
            "dimensions": len(vector),
            "model": meta.get("model"),
            "latency_ms": int((_time.monotonic() - t0) * 1000),
        })
    except Exception as e:
        return JSONResponse(content={
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "latency_ms": int((_time.monotonic() - t0) * 1000),
        })
