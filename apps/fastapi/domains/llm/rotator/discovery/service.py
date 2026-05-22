"""
Provider discovery service — live parallel fan-out across provider `/v1/models`.

DESIGN (2026-05-13): Replaces the static-list rotator catalog in llm_chain.py
where every model was hardcoded and EOLs only surfaced as 410/404 cascade
exhaustions at call time. This module hits each provider's discovery endpoint
ON DEMAND (not on a cron, not from a cache), applies a free-tier filter per
provider, and returns one consolidated `{provider: [DiscoveryRecord, ...]}`.

The rotator builder calls `list_all_alive_models()` whenever it (re)materializes
its LiteLLM Router. Each call is fresh — no staleness, no cache invalidation,
no CI cron to maintain. Total latency = max(provider response times) ≈ 0.5-1.5s
because all 8 providers are fetched in parallel via asyncio.gather.

Composition (decoupled layers):
   Discovery  →  Model Card enrichment  →  Rotator builder  →  LiteLLM Router
   (here)        (config/model_catalog)    (llm_chain.py)      (existing)

Provider matrix (validated 2026-05-13):

  Provider     Endpoint                                              Free-filter rule
  ──────────   ──────────────────────────────────────────────────    ─────────────────────────────
  groq         api.groq.com/openai/v1/models                         all (account-tier gate)
  nim          integrate.api.nvidia.com/v1/models                    all (account-tier gate)
  cerebras     api.cerebras.ai/v1/models                             all (account-tier gate)
  mistral      api.mistral.ai/v1/models                              not deprecated (date < now)
  gemini       generativelanguage.googleapis.com/v1beta/models       free-tier name prefixes
  sambanova    api.sambanova.ai/v1/models                            pricing.prompt == 0  (DISABLED)
  deepseek     api.deepseek.com/v1/models                            paid-only            (DISABLED)

OTel metrics emitted per call (for the FastAPI `/admin/rotator/models` route
and rotator-rebuild call sites):
  dd.rotator_models_alive            Gauge       per-provider live model count
  dd.rotator_discovery_duration_s    Histogram   per-call wall-clock
  dd.rotator_discovery_error_total   Counter     per-(provider, error_type) fetch failures

Fail-soft: a provider that errors during a fan-out returns [] for that
provider only. The other 7 still produce results. Caller decides whether
empty == failure or empty == "provider has no free models today."
"""
from __future__ import annotations
import datetime as dt
import time
import httpx
import os
import asyncio
import logging

from core.otel_setup import get_meter

from .constants import (
    _GEMINI_FREE_NAME_PREFIXES,
    DISCOVERY_HTTP_TIMEOUT_S,
    PROVIDERS,
    _metric_instruments
)
from .types import (
    ProviderConfig,
    DiscoveryRecord,
    FreeFilter,
)


logger = logging.getLogger(__name__)


def _filter_all(_m: dict) -> bool:
    return True


def _filter_mistral(m: dict) -> bool:
    """Drop models whose deprecation date is in the past."""
    dep = m.get("deprecation") or m.get("deprecation_date")
    if not dep:
        return True
    try:
        deadline = dt.datetime.fromisoformat(str(dep).replace("Z", "+00:00"))
        return deadline.timestamp() > time.time()
    except Exception:
        return True


def _filter_gemini(m: dict) -> bool:
    name = (m.get("name") or "").strip()
    return name.startswith(_GEMINI_FREE_NAME_PREFIXES)


def _filter_sambanova_pricing(m: dict) -> bool:
    """pricing.prompt == 0 AND pricing.completion == 0 → truly free."""
    pricing = m.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1)) == 0.0 and \
               float(pricing.get("completion", 1)) == 0.0
    except (TypeError, ValueError):
        return False


def _filter_always_false(_m: dict) -> bool:
    """For providers held disabled (paywalled, etc)."""
    return False


# FreeFilter enum value -> predicate. PROVIDERS (constants.py) stores the enum
# member; this dispatch keeps the predicate functions in service.py, so there
# is no constants -> service circular import.
_FILTER_DISPATCH = {
    FreeFilter.ALL: _filter_all,
    FreeFilter.MISTRAL: _filter_mistral,
    FreeFilter.GEMINI: _filter_gemini,
    FreeFilter.SAMBANOVA_PRICING: _filter_sambanova_pricing,
    FreeFilter.ALWAYS_FALSE: _filter_always_false,
}


# =============================================================================
# Response shape normalizers
# =============================================================================
def _normalize_response(shape: str, body: dict) -> list[dict]:
    """Provider responses → list of model dicts."""
    if shape == "gemini":
        return list(body.get("models") or [])
    # OpenAI-compatible: response['data'] is the list
    return list(body.get("data") or [])


def _model_id(provider: str, raw: dict) -> str:
    """Extract canonical model id from a provider response item."""
    if provider == "gemini":
        # Gemini returns 'name': 'models/gemini-2.5-pro' — strip the prefix
        return (raw.get("name") or "").removeprefix("models/")
    return str(raw.get("id") or raw.get("name") or "")


# =============================================================================
# HTTP fetch (single provider)
# =============================================================================
async def _fetch_provider(
    client: httpx.AsyncClient,
    cfg: ProviderConfig,
) -> list[DiscoveryRecord]:
    """One provider's /v1/models call → list of free-tier DiscoveryRecord.

    Returns [] on auth-missing / network error / non-2xx. Caller treats empty
    as "no signal from this provider on this call" — other providers still
    contribute.
    """
    api_key = os.environ.get(cfg.key_env, "").strip()
    if not api_key:
        logger.info(
            f"[discovery] {cfg.name}: {cfg.key_env} unset — skipping"
        )
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
        logger.warning(
            f"[discovery] {cfg.name} HTTP {e.response.status_code}: "
            f"{str(e)[:200]}"
        )
        _record_discovery_error(cfg.name, err_type)
        return []
    except Exception as e:
        err_type = type(e).__name__
        logger.warning(
            f"[discovery] {cfg.name} fetch failed: {err_type}: {str(e)[:200]}"
        )
        _record_discovery_error(cfg.name, err_type)
        return []
    items = _normalize_response(cfg.response_shape, body)
    filtered = [m for m in items if _FILTER_DISPATCH[cfg.free_filter](m)]
    now = time.time()
    records = [
        DiscoveryRecord(
            provider = cfg.name,
            model_id = _model_id(cfg.name, m),
            fetched_at = now,
            raw = m,
        )
        for m in filtered
        if _model_id(cfg.name, m)
    ]
    return records


# =============================================================================
# Public API — main entry point
# =============================================================================
async def list_all_alive_models(
    *,
    only_providers: list[str] | None = None,
) -> dict[str, list[DiscoveryRecord]]:
    """Fan out across all enabled providers' /v1/models in parallel.

    Returns {provider: [DiscoveryRecord, ...]} containing only providers that
    are enabled and whose free-tier filter accepted at least one model. A
    provider that errors out is present with an empty list (caller can detect
    via empty + OTel error counter).

    Args:
        only_providers: optional subset to query. None = all enabled providers.
                        Useful for the rotator's per-group rebuild path or for
                        provider-specific health checks.
    """
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
                f"[discovery] {cfg.name} task raised: "
                f"{type(result).__name__}: {result}"
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
        f"{sum(len(v) for v in out.values())} models across "
        f"{len(out)} providers"
    )
    return out


def list_all_alive_models_sync(
    *,
    only_providers: list[str] | None = None,
) -> dict[str, list[DiscoveryRecord]]:
    """Sync wrapper for non-async callers (Celery task body, debug CLI).

    Don't call from within an event loop (FastAPI request handler). Use
    `await list_all_alive_models(...)` there.
    """
    return asyncio.run(
        list_all_alive_models(only_providers = only_providers))


def flat_alive_list(
    by_provider: dict[str, list[DiscoveryRecord]],
) -> list[DiscoveryRecord]:
    """Flatten {provider: [records]} → single list (rotator-builder convenience)."""
    out: list[DiscoveryRecord] = []
    for records in by_provider.values():
        out.extend(records)
    return out


# =============================================================================
# OTel metric helpers
# =============================================================================
def _ensure_metrics() -> dict[str, Any]:
    """Lazy-create OTel instruments. No-op if otel_setup didn't initialize."""

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
        logger.info(
            f"[discovery] {len(_metric_instruments)} OTel instruments registered"
        )
    except Exception as e:
        logger.warning(
            f"[discovery] OTel instrument init failed: "
            f"{type(e).__name__}: {e}"
        )
    return _metric_instruments


def _record_models_alive(provider: str, count: int) -> None:
    inst = _ensure_metrics()
    g = inst.get("alive_gauge")
    if g is None:
        return
    try:
        g.set(
            count, 
            attributes = {
                "provider": provider})
    except Exception:
        pass


def _record_discovery_duration(duration_s: float) -> None:
    inst = _ensure_metrics()
    h = inst.get("duration_hist")
    if h is None:
        return
    try:
        h.record(duration_s)
    except Exception:
        pass


def _record_discovery_error(provider: str, error_type: str) -> None:
    inst = _ensure_metrics()
    c = inst.get("error_counter")
    if c is None:
        return
    try:
        c.add(
            1, 
            attributes = {
                "provider": provider, 
                "error_type": error_type})
    except Exception:
        pass