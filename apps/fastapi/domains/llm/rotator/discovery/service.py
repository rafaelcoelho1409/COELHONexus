"""Imperative Shell — parallel /v1/models fan-out, key validation, OTel.

Each call is fresh — no cron, no staleness. Total latency = max(provider
response times) ≈ 0.5-1.5s because all enabled providers run in parallel.
Fail-soft: a provider that errors returns [] for itself; the others still
contribute.

OTel metrics:
    dd.rotator_models_alive          Gauge       per-provider live model count
    dd.rotator_discovery_duration_s  Histogram   per-call wall-clock
    dd.rotator_discovery_error_total Counter     per-(provider, error_type)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from core.otel import get_meter
from domains.llm.credentials import resolve_key

from .config import PROVIDERS
from .domain import FILTER_DISPATCH, model_id, normalize_response
from .entities import DiscoveryRecord, ProviderConfig
from .params import DISCOVERY_HTTP_TIMEOUT_S


logger = logging.getLogger(__name__)


_metric_instruments: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# HTTP fetch (single provider)
# --------------------------------------------------------------------------- #
async def _fetch_provider(
    client: httpx.AsyncClient,
    cfg: ProviderConfig,
) -> list[DiscoveryRecord]:
    """One provider's /v1/models → free-tier records. [] on missing key / network
    error / non-2xx — caller treats empty as "no signal from this provider"."""
    api_key = resolve_key(cfg.key_env)
    if not api_key:
        logger.info(f"[discovery] {cfg.name}: {cfg.key_env} unset (store + env) — skipping")
        return []
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, str] = {}
    if cfg.auth_style == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif cfg.auth_style == "query-key":
        params["key"] = api_key
    try:
        resp = await client.get(
            cfg.url,
            headers = headers,
            params = params,
            timeout = DISCOVERY_HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPStatusError as e:
        err_type = f"http_{e.response.status_code}"
        logger.warning(f"[discovery] {cfg.name} HTTP {e.response.status_code}: {str(e)[:200]}")
        _record_discovery_error(cfg.name, err_type)
        return []
    except Exception as e:
        err_type = type(e).__name__
        logger.warning(f"[discovery] {cfg.name} fetch failed: {err_type}: {str(e)[:200]}")
        _record_discovery_error(cfg.name, err_type)
        return []
    items = normalize_response(cfg.response_shape, body)
    filtered = [m for m in items if FILTER_DISPATCH[cfg.free_filter](m)]
    now = time.time()
    return [
        DiscoveryRecord(
            provider = cfg.name, 
            model_id = mid, 
            fetched_at = now, 
            raw = m)
        for m in filtered
        if (mid := model_id(cfg.name, m))
    ]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
async def list_all_alive_models(
    *,
    only_providers: list[str] | None = None,
) -> dict[str, list[DiscoveryRecord]]:
    """Parallel fan-out across enabled providers. {provider: [records]}.
    Errored providers appear with [] (caller detects via OTel error counter)."""
    start = time.time()
    selected = [
        cfg for cfg in PROVIDERS.values()
        if cfg.enabled and (only_providers is None or cfg.name in only_providers)
    ]
    if not selected:
        return {}
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_fetch_provider(client, cfg) for cfg in selected],
            return_exceptions = True,
        )
    out: dict[str, list[DiscoveryRecord]] = {}
    for cfg, result in zip(selected, results):
        if isinstance(result, Exception):
            logger.warning(
                f"[discovery] {cfg.name} task raised: {type(result).__name__}: {result}"
            )
            _record_discovery_error(cfg.name, type(result).__name__)
            out[cfg.name] = []
            continue
        out[cfg.name] = result
        _record_models_alive(cfg.name, len(result))
    duration = time.time() - start
    _record_discovery_duration(duration)
    logger.info(
        f"[discovery] fan-out complete in {duration:.2f}s — "
        f"{sum(len(v) for v in out.values())} models across {len(out)} providers"
    )
    return out


def required_providers() -> list[str]:
    """Provider ids whose key is MANDATORY (e.g. NIM — embeddings + reranking)."""
    return [pid for pid, cfg in PROVIDERS.items() if cfg.required]


def missing_required_keys() -> list[dict]:
    """Required providers with no resolvable key. Empty == ready. Gates DD
    runs + drives the /settings readiness banner."""
    out: list[dict] = []
    for pid, cfg in PROVIDERS.items():
        if cfg.required and not resolve_key(cfg.key_env):
            out.append({"id": pid, "key_env": cfg.key_env})
    return out


async def probe_provider_key(
    provider_id: str,
    api_key: str | None = None,
) -> dict:
    """Validate a provider key by hitting its /v1/models. `api_key=None` →
    resolve via store/env (test the current key). A passed key is probed
    directly and NOT stored (test-before-save).

    status ∈ {reachable, missing_key, invalid_key, rate_limited, unreachable,
              unknown_provider}. 429 → ok=True/rate_limited (key authenticated)."""
    cfg = PROVIDERS.get(provider_id)
    base = {"n_free_models": 0, "n_total_models": 0}
    if cfg is None:
        return {"ok": False, "status": "unknown_provider",
                "error": f"unknown provider {provider_id!r}", **base}
    key = ((api_key if api_key is not None else resolve_key(cfg.key_env)) or "").strip()
    if not key:
        return {"ok": False, "status": "missing_key",
                "error": f"{cfg.key_env} not set", **base}
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, str] = {}
    if cfg.auth_style == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif cfg.auth_style == "query-key":
        params["key"] = key
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                cfg.url, 
                headers = headers, 
                params = params,
                timeout = DISCOVERY_HTTP_TIMEOUT_S)
    except Exception as e:
        return {"ok": False, "status": "unreachable",
                "error": f"{type(e).__name__}: {str(e)[:200]}", **base}
    if resp.status_code in (401, 403):
        return {"ok": False, "status": "invalid_key",
                "error": f"HTTP {resp.status_code}", **base}
    if resp.status_code == 429:
        return {"ok": True, "status": "rate_limited",
                "error": "HTTP 429 (key valid, throttled)", **base}
    if resp.status_code >= 400:
        return {"ok": False, "status": "unreachable",
                "error": f"HTTP {resp.status_code}: {resp.text[:160]}", **base}
    try:
        body = resp.json()
    except Exception:
        body = {}
    items = normalize_response(cfg.response_shape, body)
    free = [m for m in items if FILTER_DISPATCH[cfg.free_filter](m)]
    return {"ok": True, "status": "reachable", "error": None,
            "n_free_models": len(free), "n_total_models": len(items)}


async def list_provider_free_models(provider_id: str) -> list[str]:
    """Free-tier model ids for ONE provider (UI available-models list). Bypasses
    the registry `enabled` flag so a user with a key for an otherwise-disabled
    provider still sees its models."""
    cfg = PROVIDERS.get(provider_id)
    if cfg is None:
        return []
    async with httpx.AsyncClient() as client:
        records = await _fetch_provider(client, cfg)
    return sorted(r.model_id for r in records if r.model_id)


def list_all_alive_models_sync(
    *,
    only_providers: list[str] | None = None,
) -> dict[str, list[DiscoveryRecord]]:
    """Sync wrapper for non-async callers (Celery task body, debug CLI).
    Do NOT call from inside an event loop."""
    return asyncio.run(list_all_alive_models(only_providers = only_providers))


# --------------------------------------------------------------------------- #
# OTel instruments
# --------------------------------------------------------------------------- #
def _ensure_metrics() -> dict[str, Any]:
    if _metric_instruments:
        return _metric_instruments
    try:
        meter = get_meter()
        if meter is None:
            return _metric_instruments
        _metric_instruments["alive_gauge"] = meter.create_gauge(
            name = "dd.rotator_models_alive",
            description = "Free-tier models alive per provider after last discovery fan-out",
        )
        _metric_instruments["duration_hist"] = meter.create_histogram(
            name = "dd.rotator_discovery_duration_seconds",
            description = "Wall-clock for one full discovery fan-out",
            unit = "s",
        )
        _metric_instruments["error_counter"] = meter.create_counter(
            name = "dd.rotator_discovery_error_total",
            description = "Discovery fetch errors — labels: provider, error_type",
        )
        logger.info(f"[discovery] {len(_metric_instruments)} OTel instruments registered")
    except Exception as e:
        logger.warning(f"[discovery] OTel instrument init failed: {type(e).__name__}: {e}")
    return _metric_instruments


def _record_models_alive(provider: str, count: int) -> None:
    g = _ensure_metrics().get("alive_gauge")
    if g is None:
        return
    try:
        g.set(count, attributes = {"provider": provider})
    except Exception:
        pass


def _record_discovery_duration(duration_s: float) -> None:
    h = _ensure_metrics().get("duration_hist")
    if h is None:
        return
    try:
        h.record(duration_s)
    except Exception:
        pass


def _record_discovery_error(provider: str, error_type: str) -> None:
    c = _ensure_metrics().get("error_counter")
    if c is None:
        return
    try:
        c.add(1, attributes = {"provider": provider, "error_type": error_type})
    except Exception:
        pass
