"""Redis cache for paper extractions (Wave 1.7 — 2026-06-16).

Caches the structured 5-field extraction (`problem`, `method`, `math`,
`how_to_build`, `money_angle`) that the `deep_read` subagent produces
per arxiv_id. Subsequent scans that surface the same paper hit the
cache and skip the LLM extraction entirely.

Cache key: `rr:cache:extraction:{prompt_version}:{arxiv_id}`
TTL: 7 days (arxiv papers are immutable; the rate-limiting factor is
prompt-version churn — bump `EXTRACTION_PROMPT_VERSION` when the
deep_read system prompt or 5-field rubric changes meaningfully).

Mirrors the content-address pattern Planner/Synth use with
`manifest_hash → MinIO` — but lighter (Redis, not MinIO) because
each extraction is ~2 KB (vs ~50 KB per Planner outline cache).

Two integration points:
  1. `write_extraction` fs-tool: every successful extraction is
     written to cache (build-up phase). Best-effort — Redis blips
     never block the fs_write.
  2. `prefill_extractions_from_cache` (task.py): runs AFTER triage
     emits `top_n.json` and BEFORE the orchestrator's deep_read
     fan-out. Iterates top_n, checks cache, pre-populates
     `fs/extractions/{arxiv_id}.json` for hits. The orchestrator's
     PhaseEnforcerMiddleware sees the existing extractions and
     dispatches deep_read ONLY for the cache misses.

Phase 1 (this commit) wires #1 + #2. Hit-rate observability lives
on the per-scan LLM counter under `phase=cache`.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis.asyncio as redis_aio


logger = logging.getLogger(__name__)


# Bump when the deep_read system prompt or 5-field rubric materially
# changes; old cached extractions remain under their old version key
# and naturally expire via TTL. Cheap to bump; no purge needed.
EXTRACTION_PROMPT_VERSION = "v1"

# 7 days — matches the radar's typical scan cadence (operator runs the
# same topic weekly to track new releases). Long enough for repeat
# scans to benefit, short enough that prompt-version churn doesn't
# leak stale data forever.
EXTRACTION_TTL_S = 7 * 24 * 3600


def _cache_key(arxiv_id: str) -> str:
    """Redis key — prompt-version namespaced so cache survives a
    prompt-version bump (old keys expire via TTL)."""
    aid = (arxiv_id or "").strip()
    if not aid:
        raise ValueError("arxiv_id is required for extraction cache")
    return f"rr:cache:extraction:{EXTRACTION_PROMPT_VERSION}:{aid}"


async def _redis() -> redis_aio.Redis | None:
    """Lazy Redis client — None on env-misconfig so callers fall back
    gracefully (write_extraction proceeds without caching; prefill
    returns 0 hits)."""
    if "REDIS_HOST" not in os.environ:
        return None
    host = os.environ["REDIS_HOST"].strip()
    if not host:
        return None
    port = (
        os.environ["REDIS_PORT"].strip()
        if "REDIS_PORT" in os.environ else "6379"
    )
    password = (
        os.environ["REDIS_PASSWORD"].strip()
        if "REDIS_PASSWORD" in os.environ else ""
    )
    url = (
        f"redis://:{password}@{host}:{port}"
        if password else f"redis://{host}:{port}"
    )
    try:
        return redis_aio.from_url(
            url, socket_connect_timeout = 3.0, socket_timeout = 5.0,
        )
    except Exception as e:
        logger.warning(f"[rr-cache] redis init failed: {e}")
        return None


async def get_extraction(arxiv_id: str) -> dict[str, Any] | None:
    """Look up a cached extraction by arxiv_id under the current prompt
    version. Returns None on miss / Redis unavailable / parse error."""
    rds = await _redis()
    if rds is None:
        return None
    try:
        raw = await rds.get(_cache_key(arxiv_id))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        logger.debug(
            f"[rr-cache] get failed for {arxiv_id}: "
            f"{type(e).__name__}: {e}"
        )
        return None
    finally:
        try:
            await rds.aclose()
        except Exception:
            pass


async def set_extraction(
    arxiv_id: str, extraction: dict[str, Any],
) -> bool:
    """Persist an extraction under (prompt_version, arxiv_id). Best-effort
    — returns False on Redis unavailable / write failure, never raises."""
    rds = await _redis()
    if rds is None:
        return False
    try:
        await rds.set(
            _cache_key(arxiv_id),
            json.dumps(extraction, default=str),
            ex = EXTRACTION_TTL_S,
        )
        return True
    except Exception as e:
        logger.debug(
            f"[rr-cache] set failed for {arxiv_id}: "
            f"{type(e).__name__}: {e}"
        )
        return False
    finally:
        try:
            await rds.aclose()
        except Exception:
            pass


def set_extraction_sync(arxiv_id: str, extraction: dict[str, Any]) -> bool:
    """Sync facade for callers inside fs_tools (which are @tool functions
    invoked synchronously by langgraph's ToolNode). Spawns a private
    asyncio.run if no loop is active; otherwise schedules on the running
    loop. Best-effort — silent on failure."""
    import asyncio as _asyncio
    try:
        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — synchronous fs_tool call from Celery
            # worker not inside the agent's asyncio.run scope. Use a
            # private loop.
            return _asyncio.run(set_extraction(arxiv_id, extraction))
        # We're inside a running loop (the agent's). Schedule the
        # write fire-and-forget; the loop will pick it up.
        loop.create_task(set_extraction(arxiv_id, extraction))
        return True
    except Exception as e:
        logger.debug(
            f"[rr-cache] set_sync failed for {arxiv_id}: "
            f"{type(e).__name__}: {e}"
        )
        return False


async def prefill_extractions_from_cache(
    scan_id: str,
    top_n: list[dict[str, Any]],
) -> list[str]:
    """For each paper in top_n with a cache hit, write its extraction to
    the scan's virtual fs. Returns the LIST of arxiv_ids that hit the
    cache. Called from `triage_candidates` AFTER it emits top_n.json and
    BEFORE the orchestrator dispatches the deep_read fan-out.

    The returned list is surfaced verbatim in triage's return string so
    the orchestrator can SKIP `task(subagent_type="deep_read", ...)`
    dispatches for those papers — eliminating the redundant re-extraction
    loop observed in scan `157644c6` (the orchestrator's completionist
    behavior was re-dispatching deep_read AFTER synthesis when it couldn't
    point to a `task()` log entry proving extractions were "its own").
    """
    # Local imports avoid circulars (extraction_cache → fs_tools → llm_counter → ...).
    from ..agent.tools.state import fs_write
    from ..agent.keys import fs_extraction_path

    if not top_n:
        return []
    rds = await _redis()
    if rds is None:
        return []
    cached_ids: list[str] = []
    try:
        for paper in top_n:
            if not isinstance(paper, dict):
                continue
            aid = paper.get("arxiv_id")
            if not isinstance(aid, str) or not aid:
                continue
            try:
                raw = await rds.get(_cache_key(aid))
            except Exception:
                continue
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            # Ensure arxiv_id is set on the cached payload (defensive —
            # the writer should already include it).
            data.setdefault("arxiv_id", aid)
            try:
                fs_write(scan_id, fs_extraction_path(aid), data)
                cached_ids.append(aid)
            except Exception as e:
                logger.warning(
                    f"[rr-cache] prefill fs_write failed for {aid}: {e}"
                )
        if cached_ids:
            logger.info(
                f"[rr-cache] prefilled {len(cached_ids)}/{len(top_n)} extractions "
                f"from cache for scan_id={scan_id} ids={cached_ids}"
            )
        return cached_ids
    finally:
        try:
            await rds.aclose()
        except Exception:
            pass
