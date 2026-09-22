"""BYOK API-key management for FastMCP source tools. Same MinIO+Fernet store as the endpoint settings (different whitelist)."""
from __future__ import annotations

import domains
from . import params, schemas, service

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/keys")
def list_tool_keys() -> JSONResponse:
    """All managed tool keys + their current status."""
    return JSONResponse({"keys": [service._view(d) for d in params.TOOL_KEYS]})


@router.post("/keys/{key_env}")
async def set_tool_key(key_env: str, body: schemas.SetToolKeyBody) -> JSONResponse:
    """Save (or replace) a tool key. Optional probe before save (force=true skips)."""
    d = service._require_def(key_env)
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(400, "empty api_key")

    if not body.force:
        probe = await service._test_key(d, api_key)
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
        status = await run_in_threadpool(domains.settings.credentials.service.get_store().set_key, d.key_env, api_key)
    except domains.settings.credentials.errors.UnmanagedKeyEnv as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    return JSONResponse({"saved": True, "status": asdict(status)})


@router.delete("/keys/{key_env}")
async def delete_tool_key(key_env: str) -> JSONResponse:
    d = service._require_def(key_env)
    try:
        status = await run_in_threadpool(domains.settings.credentials.service.get_store().delete_key, d.key_env)
    except domains.settings.credentials.errors.UnmanagedKeyEnv as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"deleted": True, "status": asdict(status)})


@router.post("/keys/{key_env}/test")
async def test_tool_key(key_env: str) -> JSONResponse:
    """Test the CURRENTLY-STORED key (env or user-saved) against the source's API."""
    d = service._require_def(key_env)
    api_key = await run_in_threadpool(domains.settings.credentials.service.get_store().resolve_key, d.key_env)
    if not api_key:
        return JSONResponse(
            {"ok": False, "reason": "no key stored — paste one above first"}
        )
    probe = await service._test_key(d, api_key)
    return JSONResponse(probe)


