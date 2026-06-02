"""BYOK settings API — provider keys + provider/model selection.

The FastHTML `/settings` page (a server-side BFF) drives every endpoint here
through the `/api/*` reverse proxy, so raw keys travel browser→FastHTML→FastAPI
once on save and NEVER come back: responses only ever carry masked status
(`has_key`/`source`/`last4`).

Routes (mounted at /api/v1/llm/settings):
  GET    /providers              — fast: masked key status + enable + mode (no net)
  GET    /providers/{id}/models  — net: discovered free models + current selection
  POST   /providers/{id}/key     — test-connect → encrypt+store → reset rotator
  DELETE /providers/{id}/key     — drop user key → revert to env fallback
  POST   /providers/{id}/test    — probe the CURRENT resolved key
  PATCH  /providers/{id}         — enable / disable a provider
  POST   /providers/{id}/models  — set mode (all|custom) + custom selection

Every mutation calls `reset_rotator()` so the change propagates to all processes
(the rotator bumps a Redis generation; workers rebuild on next access).
Selection lives in the credential store's plaintext `llm/settings.json`:
  {"enabled":[provider...], "mode":{provider:"all|custom"}, "selected":{provider:[id...]}}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from domains.llm.credentials import get_store
from domains.llm.rotator.chain import reset_rotator
from domains.llm.rotator.discovery import (
    list_provider_free_models,
    missing_required_keys,
    probe_provider_key,
)
from domains.llm.rotator.discovery.constants import PROVIDERS


logger = logging.getLogger(__name__)

router = APIRouter()


# Display metadata. `kind` drives the free/paid badge (all free for now).
_PROVIDER_META: dict[str, dict] = {
    "groq":      {"name": "Groq",          "kind": "free"},
    "nim":       {"name": "NVIDIA NIM",    "kind": "free"},
    "cerebras":  {"name": "Cerebras",      "kind": "free"},
    "mistral":   {"name": "Mistral",       "kind": "free"},
    "gemini":    {"name": "Google Gemini", "kind": "free"},
    "sambanova": {"name": "SambaNova",     "kind": "free"},
    "deepseek":  {"name": "DeepSeek",      "kind": "free"},
}


class KeyBody(BaseModel):
    api_key: str = Field(min_length=1)
    force: bool = False     # store even if the test-connect probe fails


class EnableBody(BaseModel):
    enabled: bool


class ModelsBody(BaseModel):
    mode: Literal["all", "custom"]
    selected: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# selection helpers (sync — run in threadpool from the async routes)
# --------------------------------------------------------------------------- #
def _registry_default_enabled() -> list[str]:
    return [pid for pid, cfg in PROVIDERS.items() if cfg.enabled]


def _ensure_enabled(settings: dict, pid: str, on: bool) -> dict:
    """Toggle one provider in the enabled list. Seeds a fresh list from the
    registry defaults the first time (so turning ONE provider on/off doesn't
    silently disable all the others)."""
    en = settings.get("enabled")
    if en is None:
        en = _registry_default_enabled()
    s = set(en)
    s.add(pid) if on else s.discard(pid)
    settings["enabled"] = sorted(s)
    return settings


def _provider_view(pid: str, settings: dict) -> dict:
    cfg = PROVIDERS[pid]
    status = get_store().key_status(cfg.key_env)
    en_list = settings.get("enabled")
    enabled = (pid in en_list) if en_list is not None else cfg.enabled
    mode = (settings.get("mode") or {}).get(pid, "all")
    selected = (settings.get("selected") or {}).get(pid, [])
    meta = _PROVIDER_META.get(pid, {"name": pid, "kind": "free"})
    return {
        "id": pid,
        "name": meta["name"],
        "kind": meta["kind"],
        "key_env": cfg.key_env,
        "registry_enabled": cfg.enabled,
        "required": getattr(cfg, "required", False),
        "enabled": enabled,
        "mode": mode,
        "selected_count": len(selected),
        **status,   # has_key, source, last4
    }


def _all_provider_views() -> list[dict]:
    settings = get_store().read_settings() or {}
    return [_provider_view(pid, settings) for pid in PROVIDERS]


def _enable_and_default_mode(pid: str) -> None:
    """On a successful key save: enable the provider + default it to All-free
    (auto-include newly discovered models) unless the user already chose a mode."""
    store = get_store()
    settings = store.read_settings() or {}
    _ensure_enabled(settings, pid, on=True)
    modes = settings.get("mode") or {}
    modes.setdefault(pid, "all")
    settings["mode"] = modes
    store.write_settings(settings)


def _set_enabled(pid: str, on: bool) -> dict:
    store = get_store()
    settings = store.read_settings() or {}
    _ensure_enabled(settings, pid, on=on)
    store.write_settings(settings)
    return _provider_view(pid, settings)


def _set_models(pid: str, mode: str, selected: list[str]) -> dict:
    store = get_store()
    settings = store.read_settings() or {}
    modes = settings.get("mode") or {}
    sel = settings.get("selected") or {}
    modes[pid] = mode
    sel[pid] = sorted(set(selected)) if mode == "custom" else []
    settings["mode"] = modes
    settings["selected"] = sel
    store.write_settings(settings)
    return _provider_view(pid, settings)


def _require_provider(pid: str) -> None:
    if pid not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@router.get("/providers")
async def list_providers() -> JSONResponse:
    views = await run_in_threadpool(_all_provider_views)
    missing = await run_in_threadpool(missing_required_keys)
    return JSONResponse(content={
        "providers": views,
        "ready": not missing,                # required keys all present?
        "missing_required": missing,         # [{id, key_env}] still unset
    })


@router.get("/readiness")
async def readiness() -> JSONResponse:
    """Is the rotator runnable? Required keys (NVIDIA NIM — embeddings +
    reranking) must be present. DD run-start endpoints gate on this; the UI
    shows a banner. `ready:false` → set the missing key(s) in Settings."""
    missing = await run_in_threadpool(missing_required_keys)
    return JSONResponse(content={"ready": not missing, "missing_required": missing})


@router.get("/providers/health")
async def providers_health() -> JSONResponse:
    """Probe every keyed provider in parallel (the UI 'Test all' button + an
    ops one-shot). Periodic re-check without Celery beat: a dashboard/uptime
    monitor can poll this endpoint."""
    statuses = await run_in_threadpool(
        lambda: {pid: get_store().key_status(PROVIDERS[pid].key_env) for pid in PROVIDERS}
    )
    keyed = [pid for pid, st in statuses.items() if st["has_key"]]
    probes = await asyncio.gather(*[probe_provider_key(pid, None) for pid in keyed])
    return JSONResponse(content={
        "results": [{"id": pid, **probe} for pid, probe in zip(keyed, probes)],
    })


@router.get("/providers/{pid}/models")
async def provider_models(pid: str) -> JSONResponse:
    _require_provider(pid)
    settings = await run_in_threadpool(lambda: get_store().read_settings() or {})
    available = await list_provider_free_models(pid)
    return JSONResponse(content={
        "id": pid,
        "available": available,
        "selected": (settings.get("selected") or {}).get(pid, []),
        "mode": (settings.get("mode") or {}).get(pid, "all"),
        "has_key": (await run_in_threadpool(
            get_store().key_status, PROVIDERS[pid].key_env))["has_key"],
    })


@router.post("/providers/{pid}/key")
async def set_provider_key(pid: str, body: KeyBody) -> JSONResponse:
    _require_provider(pid)
    cfg = PROVIDERS[pid]
    probe = await probe_provider_key(pid, api_key=body.api_key)
    if not probe["ok"] and not body.force:
        # Don't persist a key that can't authenticate; surface why.
        raise HTTPException(
            status_code=400,
            detail={"message": "key validation failed", "probe": probe},
        )
    masked = await run_in_threadpool(get_store().set_key, cfg.key_env, body.api_key)
    await run_in_threadpool(_enable_and_default_mode, pid)
    await run_in_threadpool(reset_rotator)
    return JSONResponse(content={"id": pid, "key": masked, "probe": probe})


@router.delete("/providers/{pid}/key")
async def delete_provider_key(pid: str) -> JSONResponse:
    _require_provider(pid)
    cfg = PROVIDERS[pid]
    status = await run_in_threadpool(get_store().delete_key, cfg.key_env)
    await run_in_threadpool(reset_rotator)
    return JSONResponse(content={"id": pid, **status})


@router.post("/providers/{pid}/test")
async def test_provider_key(pid: str) -> JSONResponse:
    _require_provider(pid)
    probe = await probe_provider_key(pid, api_key=None)   # current resolved key
    return JSONResponse(content={"id": pid, **probe})


@router.patch("/providers/{pid}")
async def patch_provider(pid: str, body: EnableBody) -> JSONResponse:
    _require_provider(pid)
    view = await run_in_threadpool(_set_enabled, pid, body.enabled)
    await run_in_threadpool(reset_rotator)
    return JSONResponse(content=view)


@router.post("/providers/{pid}/models")
async def set_provider_models(pid: str, body: ModelsBody) -> JSONResponse:
    _require_provider(pid)
    if body.mode == "custom" and not body.selected:
        # No-empty-pool guard: a custom provider must keep ≥1 model (else
        # switch to All or disable the provider).
        raise HTTPException(
            status_code=400,
            detail="custom mode needs at least one selected model "
                   "(use All-free, or disable the provider instead)",
        )
    view = await run_in_threadpool(_set_models, pid, body.mode, body.selected)
    await run_in_threadpool(reset_rotator)
    return JSONResponse(content=view)
