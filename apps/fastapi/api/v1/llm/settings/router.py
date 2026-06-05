"""BYOK provider keys + selection. Raw keys travel browser→FastAPI once
on save; responses carry only masked status (`has_key`/`source`/`last4`).
Every mutation calls `reset_rotator()` so the change propagates across
workers via the Redis generation bump."""
from __future__ import annotations

from .params import PROVIDER_META
from .schemas import EnableBody, KeyBody, ModelsBody

import asyncio
import logging
from dataclasses import asdict
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from domains.llm.credentials import UnmanagedKeyEnv, get_store
from domains.llm.rotator.chain import reset_rotator
from domains.llm.rotator.discovery import (
    PROVIDERS,
    list_provider_free_models,
    missing_required_keys,
    probe_provider_key,
)


logger = logging.getLogger(__name__)

router = APIRouter()








def _registry_default_enabled() -> list[str]:
    return [pid for pid, cfg in PROVIDERS.items() if cfg.enabled]


def _ensure_enabled(settings: dict, pid: str, on: bool) -> dict:
    """Seeds enabled from registry defaults on first write so toggling
    one provider doesn't silently disable all the others."""
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
    meta = PROVIDER_META.get(pid, {"name": pid, "kind": "free"})
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
        **asdict(status),    # has_key, source, last4
    }


def _all_provider_views() -> list[dict]:
    settings = get_store().read_settings() or {}
    return [_provider_view(pid, settings) for pid in PROVIDERS]


def _enable_and_default_mode(pid: str) -> None:
    """Default new providers to All-free so newly discovered models
    auto-include unless the user already chose a mode."""
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


@router.get("/providers")
async def list_providers() -> JSONResponse:
    views = await run_in_threadpool(_all_provider_views)
    missing = await run_in_threadpool(missing_required_keys)
    return JSONResponse(content={
        "providers": views,
        "ready": not missing,
        "missing_required": missing,
    })


@router.get("/readiness")
async def readiness() -> JSONResponse:
    """NVIDIA NIM required (embeddings + reranking). DD run-start
    endpoints gate on this."""
    missing = await run_in_threadpool(missing_required_keys)
    return JSONResponse(content={"ready": not missing, "missing_required": missing})


@router.get("/providers/health")
async def providers_health() -> JSONResponse:
    statuses = await run_in_threadpool(
        lambda: {pid: get_store().key_status(PROVIDERS[pid].key_env) for pid in PROVIDERS}
    )
    keyed = [pid for pid, st in statuses.items() if st.has_key]
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
            get_store().key_status, PROVIDERS[pid].key_env)).has_key,
    })


@router.post("/providers/{pid}/key")
async def set_provider_key(pid: str, body: KeyBody) -> JSONResponse:
    _require_provider(pid)
    cfg = PROVIDERS[pid]
    probe = await probe_provider_key(pid, api_key=body.api_key)
    if not probe["ok"] and not body.force:
        raise HTTPException(
            status_code=400,
            detail={"message": "key validation failed", "probe": probe},
        )
    try:
        masked = await run_in_threadpool(get_store().set_key, cfg.key_env, body.api_key)
    except UnmanagedKeyEnv as e:
        raise HTTPException(status_code=400, detail=str(e))
    await run_in_threadpool(_enable_and_default_mode, pid)
    await run_in_threadpool(reset_rotator)
    return JSONResponse(content={"id": pid, "key": asdict(masked), "probe": probe})


@router.delete("/providers/{pid}/key")
async def delete_provider_key(pid: str) -> JSONResponse:
    _require_provider(pid)
    cfg = PROVIDERS[pid]
    try:
        status = await run_in_threadpool(get_store().delete_key, cfg.key_env)
    except UnmanagedKeyEnv as e:
        raise HTTPException(status_code=400, detail=str(e))
    await run_in_threadpool(reset_rotator)
    return JSONResponse(content={"id": pid, **asdict(status)})


@router.post("/providers/{pid}/test")
async def test_provider_key(pid: str) -> JSONResponse:
    _require_provider(pid)
    probe = await probe_provider_key(pid, api_key=None)
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
        raise HTTPException(
            status_code=400,
            detail="custom mode needs at least one selected model "
                   "(use All-free, or disable the provider instead)",
        )
    view = await run_in_threadpool(_set_models, pid, body.mode, body.selected)
    await run_in_threadpool(reset_rotator)
    return JSONResponse(content=view)
