"""BYOK API-key management for FastMCP source tools. Same MinIO+Fernet store as the LLM rotator (different whitelist)."""
from __future__ import annotations

import logging
import os
from dataclasses import asdict

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from domains.llm.credentials import UnmanagedKeyEnv, get_store

from .params import TOOL_KEYS, ToolKeyDef, get_tool_key_def
from .schemas import SetToolKeyBody


logger = logging.getLogger(__name__)
router = APIRouter()


def _view(d: ToolKeyDef) -> dict:
    """`provider` (catalog) kept distinct from `KeyStatus.source` — same word would collide when flattened."""
    status = get_store().key_status(d.key_env)
    return {
        "key_env":      d.key_env,
        "display_name": d.display_name,
        "provider":     d.provider,         # e.g. "api.semanticscholar.org"
        "signup_url":   d.signup_url,
        "summary":      d.summary,
        "benefit":      d.benefit,
        **asdict(status),       # has_key, source ("user"/"env"/None), last4
    }


def _require_def(key_env: str) -> ToolKeyDef:
    d = get_tool_key_def(key_env)
    if d is None:
        raise HTTPException(404, f"unknown tool key: {key_env!r}")
    return d


@router.get("/keys")
def list_tool_keys() -> JSONResponse:
    """All managed tool keys + their current status."""
    return JSONResponse({"keys": [_view(d) for d in TOOL_KEYS]})


@router.post("/keys/{key_env}")
async def set_tool_key(key_env: str, body: SetToolKeyBody) -> JSONResponse:
    """Save (or replace) a tool key. Optional probe before save (force=true skips)."""
    d = _require_def(key_env)
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(400, "empty api_key")

    if not body.force:
        probe = await _test_key(d, api_key)
        if not probe["ok"]:
            return JSONResponse(
                status_code=422,
                content={
                    "saved": False,
                    "reason": "test-connect failed; re-submit with force=true to store anyway",
                    "probe": probe,
                },
            )

    try:
        status = await run_in_threadpool(get_store().set_key, d.key_env, api_key)
    except UnmanagedKeyEnv as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    return JSONResponse({"saved": True, "status": asdict(status)})


@router.delete("/keys/{key_env}")
async def delete_tool_key(key_env: str) -> JSONResponse:
    d = _require_def(key_env)
    try:
        status = await run_in_threadpool(get_store().delete_key, d.key_env)
    except UnmanagedKeyEnv as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"deleted": True, "status": asdict(status)})


@router.post("/keys/{key_env}/test")
async def test_tool_key(key_env: str) -> JSONResponse:
    """Test the CURRENTLY-STORED key (env or user-saved) against the source's API."""
    d = _require_def(key_env)
    api_key = await run_in_threadpool(get_store().resolve_key, d.key_env)
    if not api_key:
        return JSONResponse(
            {"ok": False, "reason": "no key stored — paste one above first"}
        )
    probe = await _test_key(d, api_key)
    return JSONResponse(probe)


async def _probe_semantic_scholar(key: str) -> dict:
    """A 1-result /paper/search call validates the key without consuming budget."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": "test", "limit": 1, "fields": "paperId"},
                headers={"x-api-key": key, "User-Agent": "COELHO-Nexus-Settings/1.0"},
            )
    except httpx.RequestError as e:
        return {"ok": False, "status": 0, "reason": f"network error: {e}"}
    return {
        "ok": r.status_code == 200,
        "status": r.status_code,
        "reason": "OK" if r.status_code == 200 else r.text[:200],
    }


_TESTERS = {
    "SEMANTIC_SCHOLAR_API_KEY": _probe_semantic_scholar,
}


async def _test_key(d: ToolKeyDef, key: str) -> dict:
    tester = _TESTERS.get(d.key_env)
    if tester is None:
        return {"ok": True, "status": 0, "reason": "no test probe — assuming OK"}
    return await tester(key)
