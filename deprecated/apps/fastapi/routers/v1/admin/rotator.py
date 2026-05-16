"""
Rotator admin routes — live provider model discovery.

GET /admin/rotator/models
    Fan-out across every enabled provider's /v1/models, return the
    consolidated free-tier-filtered list. Hits providers in parallel via
    services.discovery.list_all_alive_models; per-call latency is roughly
    the slowest single-provider response (~0.5-1.5 s typical).

    Optional query param `?provider=groq,cerebras` restricts to a subset.

GET /admin/rotator/models/{provider}
    Same fan-out but only one provider. Useful for provider health checks
    and debugging which models a single endpoint currently exposes.

GET /admin/rotator/providers
    Static metadata about configured providers — name, endpoint, enabled
    flag, auth style. Doesn't hit upstream APIs; cheap.

Response shape — `/admin/rotator/models`:

    {
      "providers": {
        "groq":     [{"provider": "groq", "model_id": "llama-3.3-70b-versatile", ...}, ...],
        "nim":      [...],
        ...
      },
      "summary": {
        "groq":     {"count": 14, "ok": true},
        "nim":      {"count": 0,  "ok": false, "note": "provider returned no models"},
        ...
      },
      "total": 87,
      "duration_s": 0.842,
      "fetched_at": 1715600000.0
    }
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from services.discovery import (
    PROVIDERS,
    list_all_alive_models,
)
from services.benchmarks import (
    STEP_WEIGHTS,
    get_benchmarks,
    normalize_model_name,
    rank_for_step,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize(records) -> list[dict[str, Any]]:
    return [asdict(r) for r in records]


@router.get(
    "/rotator/models",
    summary="Live fan-out of all provider /v1/models endpoints",
)
async def list_models(
    provider: str | None = Query(
        default=None,
        description=(
            "Comma-separated subset of provider names to query. "
            "Default = all enabled providers."
        ),
    ),
) -> dict[str, Any]:
    """Return free-tier models across all (or selected) providers, live."""
    only = None
    if provider:
        only = [p.strip() for p in provider.split(",") if p.strip()]
        unknown = [p for p in only if p not in PROVIDERS]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown provider(s): {unknown}. "
                       f"Known: {sorted(PROVIDERS.keys())}",
            )

    start = time.time()
    by_provider = await list_all_alive_models(only_providers=only)
    duration = time.time() - start

    summary = {
        name: {
            "count": len(records),
            "ok": len(records) > 0,
        }
        for name, records in by_provider.items()
    }
    for name, info in summary.items():
        if not info["ok"]:
            info["note"] = (
                "provider returned no models — see kd.rotator_discovery_error "
                "metric for cause"
            )

    return {
        "providers": {
            name: _serialize(records)
            for name, records in by_provider.items()
        },
        "summary": summary,
        "total": sum(s["count"] for s in summary.values()),
        "duration_s": round(duration, 3),
        "fetched_at": time.time(),
    }


@router.get(
    "/rotator/models/{provider_name}",
    summary="Live /v1/models fetch for a single provider",
)
async def list_models_single(provider_name: str) -> dict[str, Any]:
    """Return free-tier models for one provider."""
    if provider_name not in PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown provider: {provider_name}. "
                   f"Known: {sorted(PROVIDERS.keys())}",
        )
    by_provider = await list_all_alive_models(only_providers=[provider_name])
    records = by_provider.get(provider_name, [])
    return {
        "provider": provider_name,
        "count": len(records),
        "models": _serialize(records),
        "fetched_at": time.time(),
    }


@router.get(
    "/rotator/ranked",
    summary="Live discovery + benchmark-ranked pool for one processing step",
)
async def ranked_for_step(
    request: Request,
    step: str = Query(
        default="kd-all",
        description=(
            f"Processing step to rank for. One of: {sorted(STEP_WEIGHTS.keys())}. "
            f"Unknown step falls back to kd-all weights."
        ),
    ),
    provider: str | None = Query(
        default=None,
        description=(
            "Comma-separated subset of provider names to include. "
            "Default = all enabled providers."
        ),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Return only the top-N ranked models.",
    ),
    include_raw_scores: bool = Query(
        default=False,
        description=(
            "If true, attach the raw per-source benchmark scores for each "
            "model alongside the composite score. Useful for debugging."
        ),
    ),
) -> dict[str, Any]:
    """Live fan-out → benchmark scoring → ranked pool for a processing step.

    Pipeline:
      1. services.discovery.list_all_alive_models() — current free-tier models
      2. services.benchmarks.rank_for_step(step, alive) — composite score per model
      3. Sort descending, truncate to `limit`, optionally attach raw scores

    Use this to inspect what the rotator builder WOULD see for a given step,
    without rebuilding the LiteLLM Router. Heavy on first call (per-source
    leaderboard fetches), cheap after (90-day score cache + 7-day leaderboard
    cache).
    """
    only = None
    if provider:
        only = [p.strip() for p in provider.split(",") if p.strip()]
        unknown = [p for p in only if p not in PROVIDERS]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown provider(s): {unknown}",
            )

    if step not in STEP_WEIGHTS:
        logger.info(f"[rotator] step '{step}' unknown — using kd-all weights")

    redis = getattr(request.app.state, "redis_aio", None)

    start = time.time()
    by_provider = await list_all_alive_models(only_providers=only)
    discovery_done = time.time()

    alive = []
    for records in by_provider.values():
        alive.extend(records)

    ranked = await rank_for_step(step, alive, redis=redis)
    rank_done = time.time()

    # Build response — strip raw provider payload to keep the response small
    out_models: list[dict[str, Any]] = []
    for record, score in ranked[:limit]:
        entry: dict[str, Any] = {
            "provider": record.provider,
            "model_id": record.model_id,
            "canonical": normalize_model_name(record.model_id),
            "composite_score": round(score, 4),
        }
        if include_raw_scores:
            canonical = normalize_model_name(record.model_id)
            entry["benchmarks"] = await get_benchmarks(canonical, redis=redis)
        out_models.append(entry)

    return {
        "step": step,
        "weights": STEP_WEIGHTS.get(step, STEP_WEIGHTS["kd-all"]),
        "alive_total": len(alive),
        "ranked_returned": len(out_models),
        "discovery_s": round(discovery_done - start, 3),
        "ranking_s": round(rank_done - discovery_done, 3),
        "total_s": round(rank_done - start, 3),
        "models": out_models,
    }


@router.get(
    "/rotator/catalog-state",
    summary="Live state of the deployed Router's dynamic catalog (Phase 1)",
)
def catalog_state() -> dict[str, Any]:
    """Inspect what the currently-running Router serves per step.

    Differentiates dynamic (post-init_dynamic_catalog) vs static (fallback).
    The /rotator/ranked endpoint always computes a FRESH ranking; this one
    shows what the Router is actually configured with at runtime.
    """
    from services import llm_chain
    out: dict[str, Any] = {
        "dynamic_catalog_initialized": llm_chain._dynamic_catalog_initialized,
        "kd_dynamic_catalog_env": os.environ.get("KD_DYNAMIC_CATALOG", "<unset>"),
        "steps": {},
    }
    for step, fn in [
        ("kd-all",          llm_chain._all_entries_current),
        ("kd-synth",        llm_chain._synth_entries_current),
        ("kd-reduce-label", llm_chain._reduce_label_entries_current),
    ]:
        entries = fn()
        is_dynamic = step in llm_chain._dynamic_entries
        out["steps"][step] = {
            "source": "dynamic" if is_dynamic else "static",
            "entries_count": len(entries),
            "litellm_models": [
                e["litellm_params"]["model"] for e in entries
            ],
        }
    return out


@router.get(
    "/rotator/bandit-state",
    summary="ParetoBandit per-cell state (Phase 2)",
)
async def bandit_state(
    request: Request,
    kd_process: str | None = Query(
        default=None,
        description="Filter to one kd_process (e.g. kd-synth). Default = all.",
    ),
) -> dict[str, Any]:
    """Inspect the per-(deployment, kd_process) bandit cells in Redis.

    Each cell stores n_obs, last_updated, benchmark_prior, and current θ̂_a
    norm. Use this to verify warm-start populated cells, watch n_obs grow
    as shadow-mode runs, and confirm geometric forgetting keeps cells fresh.
    """
    from services import pareto_bandit
    redis = getattr(request.app.state, "redis_aio", None)
    pattern = f":{kd_process}" if kd_process else None
    cells = await pareto_bandit.get_all_cells(redis=redis, pattern=pattern)

    now = time.time()
    per_process: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        try:
            theta = cell.theta_hat()
            theta_norm = float((theta ** 2).sum() ** 0.5)
        except Exception:
            theta_norm = 0.0
        per_process.setdefault(cell.kd_process, []).append({
            "deployment": cell.deployment,
            "n_obs": cell.n_obs,
            "benchmark_prior": round(cell.benchmark_prior, 4),
            "theta_norm": round(theta_norm, 4),
            "age_seconds": round(now - cell.last_updated, 1),
        })

    # Sort each process's cells by n_obs descending (most-observed at top)
    for proc in per_process:
        per_process[proc].sort(key=lambda x: (-x["n_obs"], -x["benchmark_prior"]))

    return {
        "total_cells": len(cells),
        "kd_processes": sorted(per_process.keys()),
        "per_process": per_process,
    }


@router.get(
    "/rotator/providers",
    summary="Configured provider catalog (static metadata, no upstream calls)",
)
def list_providers() -> dict[str, Any]:
    """Return the provider config table without hitting any external API."""
    return {
        "providers": [
            {
                "name": cfg.name,
                "url": cfg.url,
                "key_env": cfg.key_env,
                "auth_style": cfg.auth_style,
                "response_shape": cfg.response_shape,
                "enabled": cfg.enabled,
            }
            for cfg in PROVIDERS.values()
        ],
        "enabled_count": sum(1 for c in PROVIDERS.values() if c.enabled),
        "total_count": len(PROVIDERS),
    }
