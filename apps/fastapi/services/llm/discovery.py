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
  zhipu        open.bigmodel.cn/api/paas/v4/models                   all (account-tier gate)
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

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================
DISCOVERY_HTTP_TIMEOUT_S = 15      # per-provider request budget

# Free-tier filter helpers (provider-specific)
_GEMINI_FREE_NAME_PREFIXES = (
    "models/gemini-2.5-pro",
    "models/gemini-2.5-flash",
    "models/gemini-2.5-flash-lite",
    "models/gemini-embedding",
)


# =============================================================================
# Data class
# =============================================================================
@dataclass(frozen=True)
class DiscoveryRecord:
    """One model entry as observed at fetch time."""
    provider: str
    model_id: str          # canonical id used by LiteLLM's `<provider>/<model_id>`
    fetched_at: float      # unix seconds
    raw: dict = field(default_factory=dict)  # full provider response item


# =============================================================================
# Free-tier filter callables (one per provider)
# =============================================================================
def _filter_all(_m: dict) -> bool:
    return True


def _filter_mistral(m: dict) -> bool:
    """Drop models whose deprecation date is in the past."""
    dep = m.get("deprecation") or m.get("deprecation_date")
    if not dep:
        return True
    try:
        import datetime as _dt
        deadline = _dt.datetime.fromisoformat(str(dep).replace("Z", "+00:00"))
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


# =============================================================================
# Provider config
# =============================================================================
@dataclass(frozen=True)
class ProviderConfig:
    name: str
    url: str
    key_env: str
    auth_style: str                            # "bearer" | "query-key"
    response_shape: str                        # "openai" | "gemini"
    free_filter: Callable[[dict], bool]
    enabled: bool = True


PROVIDERS: dict[str, ProviderConfig] = {
    "groq": ProviderConfig(
        name="groq",
        url="https://api.groq.com/openai/v1/models",
        key_env="GROQ_API_KEY",
        auth_style="bearer",
        response_shape="openai",
        free_filter=_filter_all,
    ),
    "nim": ProviderConfig(
        name="nim",
        url="https://integrate.api.nvidia.com/v1/models",
        key_env="NVIDIA_API_KEY",
        auth_style="bearer",
        response_shape="openai",
        free_filter=_filter_all,
    ),
    "cerebras": ProviderConfig(
        name="cerebras",
        url="https://api.cerebras.ai/v1/models",
        key_env="CEREBRAS_API_KEY",
        auth_style="bearer",
        response_shape="openai",
        free_filter=_filter_all,
    ),
    "mistral": ProviderConfig(
        name="mistral",
        url="https://api.mistral.ai/v1/models",
        key_env="MISTRAL_API_KEY",
        auth_style="bearer",
        response_shape="openai",
        free_filter=_filter_mistral,
    ),
    "gemini": ProviderConfig(
        name="gemini",
        url="https://generativelanguage.googleapis.com/v1beta/models",
        key_env="GOOGLE_API_KEY",
        auth_style="query-key",
        response_shape="gemini",
        free_filter=_filter_gemini,
    ),
    "zhipu": ProviderConfig(
        name="zhipu",
        url="https://open.bigmodel.cn/api/paas/v4/models",
        key_env="ZHIPU_API_KEY",
        auth_style="bearer",
        response_shape="openai",
        free_filter=_filter_all,    # tier is account-level; listing == callable
        enabled=False,    # DISABLED 2026-05-14 — free-tier credits exhausted
                          # mid-run (Chinese error `余额不足或无可用资源包,请充值`).
                          # Account-level quota with no graceful warning, and the
                          # cascade can re-pin to the same dead chain via Phase 1
                          # fallback, producing TERMINAL FAILURE (canary v5 ch02).
                          # Re-enable when (a) account is refilled OR (b) the
                          # chapter-pin re-pick-on-exhaustion fix lands.
    ),
    "sambanova": ProviderConfig(
        name="sambanova",
        url="https://api.sambanova.ai/v1/models",
        key_env="SAMBANOVA_API_KEY",
        auth_style="bearer",
        response_shape="openai",
        free_filter=_filter_sambanova_pricing,
        enabled=False,    # whole provider paywalled as of 2026-04-24 Run-8
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        url="https://api.deepseek.com/v1/models",
        key_env="DEEPSEEK_API_KEY",
        auth_style="bearer",
        response_shape="openai",
        free_filter=_filter_always_false,
        enabled=False,    # direct API paid-only; NIM-hosted DeepSeek is the free path
    ),
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
            headers=headers,
            params=params,
            timeout=DISCOVERY_HTTP_TIMEOUT_S,
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
    filtered = [m for m in items if cfg.free_filter(m)]
    now = time.time()
    records = [
        DiscoveryRecord(
            provider=cfg.name,
            model_id=_model_id(cfg.name, m),
            fetched_at=now,
            raw=m,
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
            return_exceptions=True,
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
    return asyncio.run(list_all_alive_models(only_providers=only_providers))


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
_metric_instruments: dict[str, Any] = {}


def _ensure_metrics() -> dict[str, Any]:
    """Lazy-create OTel instruments. No-op if otel_setup didn't initialize."""
    if _metric_instruments:
        return _metric_instruments
    try:
        from services.llm.otel_setup import get_meter
        meter = get_meter()
        if meter is None:
            return _metric_instruments
        _metric_instruments["alive_gauge"] = meter.create_gauge(
            name="dd.rotator_models_alive",
            description="Free-tier models alive per provider after last discovery fan-out",
        )
        _metric_instruments["duration_hist"] = meter.create_histogram(
            name="dd.rotator_discovery_duration_seconds",
            description="Wall-clock for one full discovery fan-out",
            unit="s",
        )
        _metric_instruments["error_counter"] = meter.create_counter(
            name="dd.rotator_discovery_error_total",
            description="Discovery fetch errors — labels: provider, error_type",
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
        g.set(count, attributes={"provider": provider})
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
        c.add(1, attributes={"provider": provider, "error_type": error_type})
    except Exception:
        pass
