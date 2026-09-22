"""tool_credentials service — key-status views, probes, and save/test orchestration."""
from __future__ import annotations

import domains
from . import entities, params

from dataclasses import asdict

import httpx
from fastapi import HTTPException


def _view(d: entities.ToolKeyDef) -> dict:
    """`provider` (catalog) kept distinct from `KeyStatus.source` — same word would collide when flattened."""
    status = domains.settings.credentials.service.get_store().key_status(d.key_env)
    return {
        "key_env":      d.key_env,
        "display_name": d.display_name,
        "provider":     d.provider,         # e.g. "api.semanticscholar.org"
        "signup_url":   d.signup_url,
        "summary":      d.summary,
        "benefit":      d.benefit,
        **asdict(status),       # has_key, source ("user"/"env"/None), last4
    }


def _require_def(key_env: str) -> entities.ToolKeyDef:
    d = params.get_tool_key_def(key_env)
    if d is None:
        raise HTTPException(404, f"unknown tool key: {key_env!r}")
    return d


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


async def _test_key(d: entities.ToolKeyDef, key: str) -> dict:
    tester = _TESTERS.get(d.key_env)
    if tester is None:
        return {"ok": True, "status": 0, "reason": "no test probe — assuming OK"}
    return await tester(key)
