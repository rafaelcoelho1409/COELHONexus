"""External endpoint settings — the two OpenAI-compatible URLs Nexus calls.

Raw keys travel browser→FastAPI once on save; responses carry only
masked status (`has_key`/`source`/`last4`). Every mutation resets the
corresponding pooled client so the change propagates to the next call.
"""
from __future__ import annotations

from .schemas import EmbeddingBody, EndpointBody

import logging
from dataclasses import asdict
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import domains


logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# LLM endpoint — the OpenAI-compatible URL the Docs Distiller / YCS / RR
# apps call. This field is the single runtime source of truth for where it
# lives — point it at any OpenAI-compatible chat endpoint. Overrides the
# LLM_ENDPOINT_* / COELHO_LLM_* env defaults when set.
# ---------------------------------------------------------------------------

_ENDPOINT_KEY_ENV = "COELHO_LLM_API_KEY"


def _endpoint_view() -> dict:
    s =domains.settings.credentials.service.get_store().read_settings() or {}
    ep = s.get("llm_endpoint") or {}
    st =domains.settings.credentials.service.get_store().key_status(_ENDPOINT_KEY_ENV)
    return {
        "url": ep.get("url") or "",
        "model": ep.get("model") or "auto",
        **asdict(st),  # has_key, source, last4
    }


def _write_endpoint(body: EndpointBody) -> None:
    store =domains.settings.credentials.service.get_store()
    s = store.read_settings() or {}
    s["llm_endpoint"] = {
        "url": body.url.strip(),
        "model": (body.model or "auto").strip() or "auto",
    }
    store.write_settings(s)
    if body.api_key is not None:
        if body.api_key.strip():
            store.set_key(_ENDPOINT_KEY_ENV, body.api_key.strip())
        else:
            try:
                store.delete_key(_ENDPOINT_KEY_ENV)
            except Exception:
                pass
    domains.settings.chat.service.reset_chat_client()


@router.get("/endpoint")
async def get_endpoint() -> JSONResponse:
    return JSONResponse(content=await run_in_threadpool(_endpoint_view))


@router.put("/endpoint")
async def put_endpoint(body: EndpointBody) -> JSONResponse:
    try:
        await run_in_threadpool(_write_endpoint, body)
    except domains.settings.credentials.errors.UnmanagedKeyEnv as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content=await run_in_threadpool(_endpoint_view))


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


# ---------------------------------------------------------------------------
# Embedding endpoint — independent connection from the LLM Endpoint above,
# same shape and same flexibility.
# ---------------------------------------------------------------------------

_EMBEDDING_KEY_ENV = "COELHO_EMBEDDING_API_KEY"


def _embedding_view() -> dict:
    s =domains.settings.credentials.service.get_store().read_settings() or {}
    ep = s.get("embedding_endpoint") or {}
    st =domains.settings.credentials.service.get_store().key_status(_EMBEDDING_KEY_ENV)
    return {
        "url": ep.get("url") or "",
        "model": ep.get("model") or "auto",
        **asdict(st),  # has_key, source, last4
    }


def _write_embedding(body: EmbeddingBody) -> None:
    store =domains.settings.credentials.service.get_store()
    s = store.read_settings() or {}
    s["embedding_endpoint"] = {
        "url": body.url.strip(),
        "model": (body.model or "auto").strip() or "auto",
    }
    store.write_settings(s)
    if body.api_key is not None:
        if body.api_key.strip():
            store.set_key(_EMBEDDING_KEY_ENV, body.api_key.strip())
        else:
            try:
                store.delete_key(_EMBEDDING_KEY_ENV)
            except Exception:
                pass
    domains.settings.embeddings.service.reset_embedding_client()


@router.get("/embedding")
async def get_embedding() -> JSONResponse:
    return JSONResponse(content=await run_in_threadpool(_embedding_view))


@router.put("/embedding")
async def put_embedding(body: EmbeddingBody) -> JSONResponse:
    try:
        await run_in_threadpool(_write_embedding, body)
    except domains.settings.credentials.errors.UnmanagedKeyEnv as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content=await run_in_threadpool(_embedding_view))


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
