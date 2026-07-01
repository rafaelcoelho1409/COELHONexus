"""Three live benchmark sources (no-auth): OpenLM Arena (HTML), oolong-tea code.json, OpenEvals."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

import httpx
import redis.asyncio as redis_aio
from rapidfuzz import fuzz as _rf_fuzz, process as _rf_process

from infra.otel import get_meter

from .config import CACHE_TTL
from .domain import (
    compute_composite_score,
    merge_leaderboards,
    normalize_model_name,
    parse_oolong_payload,
    parse_openevals_payload,
    parse_openlm_table,
)
from .keys import canonical_key, leaderboard_key, scores_key
from .params import (
    FUZZY_THRESHOLD,
    HTTP_TIMEOUT_S,
    PROVIDER_TIER,
    STEP_WEIGHTS,
)


logger = logging.getLogger(__name__)


# Module-level by design — survive across requests within one worker.
_known_canonicals: set[str] = set()
_inmem_leaderboards: dict[str, tuple[float, dict[str, dict[str, float]]]] = {}
_metric_instruments: dict[str, Any] = {}


async def canonicalize(
    provider_id: str,
    *,
    redis: redis_aio.Redis | None = None,
    fuzzy_threshold: int = FUZZY_THRESHOLD,
) -> str:
    """Redis (1y TTL) → L1 heuristic → L2 RapidFuzz against known canonicals."""
    pid = (provider_id or "").strip()
    if not pid:
        return ""
    if redis is not None:
        try:
            cached = await redis.get(canonical_key(pid))
            if cached:
                if isinstance(cached, bytes):
                    cached = cached.decode()
                _record_canonical("cache")
                _known_canonicals.add(cached)
                return cached
        except Exception as e:
            logger.debug(f"[bench] canonical cache read failed for {pid}: {e}")
    candidate = normalize_model_name(pid)
    resolved = candidate
    layer = "heuristic"
    if _known_canonicals:
        match = _rf_process.extractOne(
            candidate,
            list(_known_canonicals),
            scorer = _rf_fuzz.token_set_ratio,
        )
        if match and match[1] >= fuzzy_threshold:
            resolved = match[0]
            layer = "fuzzy"
    _known_canonicals.add(resolved)
    _record_canonical(layer)
    if redis is not None:
        try:
            await redis.set(
                canonical_key(pid), 
                resolved, 
                ex = CACHE_TTL.canonical)
        except Exception as e:
            logger.debug(f"[bench] canonical cache write failed for {pid}: {e}")
    return resolved


async def _get_cached_leaderboard(
    source: str,
    fetcher: Callable[[httpx.AsyncClient], Awaitable[dict[str, dict[str, float]]]],
    redis: redis_aio.Redis | None,
    client: httpx.AsyncClient,
) -> dict[str, dict[str, float]]:
    """L1 in-mem → L2 Redis → fetch."""
    now = time.time()
    cached = _inmem_leaderboards.get(source)
    if cached and (now - cached[0]) < CACHE_TTL.leaderboard:
        _record_cache_hit("inmem")
        return cached[1]
    if redis is not None:
        try:
            raw = await redis.get(leaderboard_key(source))
            if raw:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                _inmem_leaderboards[source] = (now, data)
                _record_cache_hit("redis_lb")
                return data
        except Exception as e:
            logger.debug(f"[bench] L2 read failed for {source}: {e}")
    t0 = time.time()
    try:
        data = await fetcher(client)
        outcome = "ok"
        logger.info(f"[bench] {source}: fetched {len(data)} models")
    except Exception as e:
        outcome = type(e).__name__
        logger.warning(f"[bench] {source} fetch failed: {outcome}: {str(e)[:200]}")
        data = {}
    _record_fetch(source, outcome, time.time() - t0)
    _inmem_leaderboards[source] = (now, data)
    if redis is not None:
        try:
            ttl = CACHE_TTL.leaderboard if data else CACHE_TTL.empty_payload
            await redis.set(leaderboard_key(source), json.dumps(data), ex=ttl)
        except Exception as e:
            logger.debug(f"[bench] L2 write failed for {source}: {e}")
    return data


_BENCH_HEADERS = {"Accept": "application/json", "User-Agent": "coelhonexus/1.0"}


async def _fetch_openlm_arena(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    resp = await client.get(
        "https://openlm.ai/chatbot-arena/",
        headers = {
            "User-Agent": "coelhonexus/1.0 (free-tier-rotator)",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout = HTTP_TIMEOUT_S,
        follow_redirects = True,
    )
    resp.raise_for_status()
    return parse_openlm_table(resp.text)


async def _fetch_oolong_code(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    """2-step: latest.json pointer → data/{path}/code.json."""
    base = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data"
    try:
        ptr = await client.get(f"{base}/latest.json", headers=_BENCH_HEADERS, timeout=HTTP_TIMEOUT_S)
        ptr.raise_for_status()
        snapshot_path = (ptr.json() or {}).get("path") or (ptr.json() or {}).get("date")
    except Exception as e:
        logger.warning(f"[bench] oolong latest pointer failed: {e}")
        return {}
    if not snapshot_path:
        return {}
    try:
        resp = await client.get(
            f"{base}/{snapshot_path}/code.json",
            headers = _BENCH_HEADERS,
            timeout = HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        return parse_oolong_payload(resp.json())
    except Exception as e:
        logger.warning(f"[bench] oolong code.json fetch failed: {e}")
        return {}


async def _fetch_openevals(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    resp = await client.get(
        "https://huggingface.co/datasets/OpenEvals/leaderboard-data/resolve/main/leaderboard.json",
        headers = _BENCH_HEADERS,
        timeout = HTTP_TIMEOUT_S,
        follow_redirects = True,
    )
    resp.raise_for_status()
    return parse_openevals_payload(resp.json())


_SOURCES: dict[str, Callable[[httpx.AsyncClient], Awaitable[dict[str, dict[str, float]]]]] = {
    "openlm_arena": _fetch_openlm_arena,
    "oolong_code":  _fetch_oolong_code,
    "openevals":    _fetch_openevals,
}


async def get_benchmarks(
    canonical_name: str,
    *,
    redis: redis_aio.Redis | None = None,
) -> dict[str, float]:
    """L3 Redis → fan out _SOURCES in parallel → merge → cache."""
    canonical = (canonical_name or "").strip().lower()
    if not canonical:
        return {}
    if redis is not None:
        try:
            cached = await redis.get(scores_key(canonical))
            if cached:
                _record_cache_hit("scores")
                return json.loads(cached if isinstance(cached, str) else cached.decode())
        except Exception as e:
            logger.debug(f"[bench] L3 read failed for {canonical}: {e}")
    async with httpx.AsyncClient() as client:
        boards = await asyncio.gather(
            *[_get_cached_leaderboard(name, fetcher, redis, client)
              for name, fetcher in _SOURCES.items()],
            return_exceptions = True,
        )
    valid = [b for b in boards if isinstance(b, dict)]
    merged = merge_leaderboards(canonical, valid)
    if redis is not None:
        try:
            ttl = CACHE_TTL.scores if merged else CACHE_TTL.empty_payload
            await redis.set(
                scores_key(canonical), 
                json.dumps(merged), 
                ex = ttl)
        except Exception as e:
            logger.debug(f"[bench] L3 write failed for {canonical}: {e}")
    return merged


async def rank_for_step(
    step: str,
    alive_models: list,
    *,
    redis: redis_aio.Redis | None = None,
) -> list[tuple[Any, float]]:
    weights = STEP_WEIGHTS.get(step, STEP_WEIGHTS["dd-all"])
    if not alive_models:
        return []
    canonicals = await asyncio.gather(
        *[canonicalize(getattr(m, "model_id", ""), redis = redis) for m in alive_models]
    )
    async with httpx.AsyncClient() as client:
        board_results = await asyncio.gather(
            *[_get_cached_leaderboard(name, fetcher, redis, client)
              for name, fetcher in _SOURCES.items()],
            return_exceptions = True,
        )
    valid_boards = [b for b in board_results if isinstance(b, dict)]
    ranked: list[tuple[Any, float]] = []
    for record, canonical in zip(alive_models, canonicals):
        scores = merge_leaderboards(canonical, valid_boards)
        composite = compute_composite_score(scores, weights)
        ranked.append((record, composite))
    ranked.sort(
        key = lambda x: (
            -x[1],
            PROVIDER_TIER.get(getattr(x[0], "provider", ""), 99),
            getattr(x[0], "model_id", ""),
        )
    )
    return ranked


def _ensure_metrics() -> dict[str, Any]:
    if _metric_instruments:
        return _metric_instruments
    try:
        meter = get_meter()
        if meter is None:
            return _metric_instruments
        _metric_instruments["fetch_counter"] = meter.create_counter(
            name = "dd.rotator_benchmark_fetch_total",
            description = "Benchmark leaderboard fetches — labels: source, outcome",
        )
        _metric_instruments["fetch_duration"] = meter.create_histogram(
            name = "dd.rotator_benchmark_fetch_duration_seconds",
            description = "Per-source leaderboard fetch wall-clock",
            unit = "s",
        )
        _metric_instruments["cache_hit"] = meter.create_counter(
            name = "dd.rotator_benchmark_cache_hit_total",
            description = "Cache hits — labels: layer ∈ {inmem, redis_lb, scores, canonical}",
        )
        _metric_instruments["canonical_counter"] = meter.create_counter(
            name = "dd.rotator_canonical_resolution_total",
            description = "Canonicalization resolutions — labels: layer ∈ {cache, heuristic, fuzzy}",
        )
        logger.info(f"[bench] {len(_metric_instruments)} OTel instruments registered")
    except Exception as e:
        logger.warning(f"[bench] OTel init failed: {type(e).__name__}: {e}")
    return _metric_instruments


def _record_fetch(source: str, outcome: str, duration_s: float) -> None:
    inst = _ensure_metrics()
    try:
        if "fetch_counter" in inst:
            inst["fetch_counter"].add(1, attributes = {"source": source, "outcome": outcome})
        if "fetch_duration" in inst:
            inst["fetch_duration"].record(duration_s, attributes = {"source": source})
    except Exception:
        pass


def _record_cache_hit(layer: str) -> None:
    inst = _ensure_metrics()
    try:
        if "cache_hit" in inst:
            inst["cache_hit"].add(1, attributes = {"layer": layer})
    except Exception:
        pass


def _record_canonical(layer: str) -> None:
    inst = _ensure_metrics()
    try:
        if "canonical_counter" in inst:
            inst["canonical_counter"].add(1, attributes = {"layer": layer})
    except Exception:
        pass
