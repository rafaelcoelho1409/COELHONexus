"""I/O orchestration for RR runtime — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §8 strict-merge: SSE events, code-synth
job status, the extraction cache, the fs mirror, and the resilient
single-LLM-call helper are 5 distinct concerns that all reduce to
"Redis I/O the rest of the domain calls into" — same role, one file,
sectioned below.

  Events            publish + subscribe phase events for SSE; task-id
                     store for the cancel button.
  Code synth status tracks an in-flight/failed Build-tab generation
                     (`domains.rr.task.run_code_synth`) so the poll
                     endpoint reports "pending" across a page refresh
                     instead of a false idle/404 while the Celery task
                     runs — the actual result lives in MinIO
                     (`stores.service.get_code_py`), this is just the
                     "still working" signal in between.
  Extraction cache  content-addressed (prompt_version, arxiv_id) → the
                     deep_read subagent's 5-field extraction. Repeat
                     scans on the same paper skip the LLM call entirely.
  fs mirror         read-side Redis mirror of the agent's per-scan
                     virtual filesystem (`agent/tools/state.py`'s
                     process-local dict), so FastAPI can read live/past
                     scan output the Celery worker's dict already lost.
  Resilient call    `resilient_ainvoke` — retry-with-backoff wrapper for
                     one-off `chain.ainvoke()` calls made OUTSIDE the
                     DeepAgents turn loop (task.py's inline backfill,
                     code_synth's generate/critique/revise rounds),
                     which don't get retry/usage-capture "for free" from
                     `RRLlmCounterCallback` the way the orchestrator +
                     subagents do.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, AsyncIterator

import redis.asyncio as redis_aio

from . import domain, keys, llm_counter, metrics, params


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Events — Redis pub/sub for live phase events + task-id store for cancel
# ---------------------------------------------------------------------------

def emit_event_sync(scan_id: str, phase: str, **fields) -> None:
    """Sync publisher for the Celery task body. Best-effort — Redis
    failure logs but does NOT raise (we don't want to abort a successful
    scan because Redis blinked at the end)."""
    event = {
        "scan_id": scan_id,
        "phase":   phase,
        "ts":      time.time(),
        **fields,
    }
    payload = json.dumps(event, default=str)
    metrics.record_phase_event(phase = phase)
    import redis as redis_sync
    try:
        r = redis_sync.from_url(
            keys.redis_url(),
            socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = params.REDIS_OP_TIMEOUT_S,
        )
        try:
            r.publish(keys.event_channel(scan_id), payload)
            key = keys.snapshot_key(scan_id)
            pipe = r.pipeline(transaction=False)
            pipe.rpush(key, payload)
            pipe.ltrim(key, -params.SNAPSHOT_MAX_EVENTS, -1)
            pipe.expire(key, params.SNAPSHOT_TTL_S)
            pipe.execute()
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[rr-events] emit_event_sync failed for {scan_id} phase={phase!r}: "
            f"{type(e).__name__}: {e}"
        )


async def emit_event(scan_id: str, phase: str, **fields) -> None:
    """Async publisher. Same payload shape as emit_event_sync."""
    event = {
        "scan_id": scan_id,
        "phase":   phase,
        "ts":      time.time(),
        **fields,
    }
    payload = json.dumps(event, default=str)
    metrics.record_phase_event(phase = phase)
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.publish(keys.event_channel(scan_id), payload)
        key = keys.snapshot_key(scan_id)
        pipe = r.pipeline(transaction=False)
        pipe.rpush(key, payload)
        pipe.ltrim(key, -params.SNAPSHOT_MAX_EVENTS, -1)
        pipe.expire(key, params.SNAPSHOT_TTL_S)
        await pipe.execute()
    except Exception as e:
        logger.warning(
            f"[rr-events] emit_event failed for {scan_id} phase={phase!r}: "
            f"{type(e).__name__}: {e}"
        )
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def store_task_id(scan_id: str, task_id: str) -> None:
    """SET `rr:{scan_id}:task_id = task_id` with `TASK_ID_TTL_S` expiry."""
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.set(keys.task_id_key(scan_id), task_id, ex=params.TASK_ID_TTL_S)
    except Exception as e:
        logger.warning(
            f"[rr-events] store_task_id failed for {scan_id}: "
            f"{type(e).__name__}: {e}"
        )
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def get_task_id(scan_id: str) -> str | None:
    """GET `rr:{scan_id}:task_id`. Returns None if the key is missing or
    Redis errors — both callers (cancel endpoint) treat None as 404."""
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        raw = await r.get(keys.task_id_key(scan_id))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception as e:
        logger.warning(
            f"[rr-events] get_task_id failed for {scan_id}: "
            f"{type(e).__name__}: {e}"
        )
        return None
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def clear_task_id(scan_id: str) -> None:
    """DEL `rr:{scan_id}:task_id` after a successful cancel so a stale
    UUID never lingers past the revoke point."""
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.delete(keys.task_id_key(scan_id))
    except Exception:
        pass
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Code synth status — in-flight/failed marker for the Build tab's async job.
# ---------------------------------------------------------------------------

async def set_code_synth_running(
    scan_id: str, arxiv_id: str, prompt_version: str, *, task_id: str,
) -> None:
    """Mark a Build-tab generation as in-flight. Read by the poll endpoint
    so a page refresh (or opening a different finding and coming back)
    shows "pending" instead of a false idle/404 while the Celery task
    (`domains.rr.task.run_code_synth`) is still running."""
    payload = json.dumps({"status": "running", "task_id": task_id, "ts": time.time()})
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.set(
            keys.code_synth_status_key(scan_id, arxiv_id, prompt_version),
            payload, ex=params.CODE_SYNTH_STATUS_TTL_S,
        )
    except Exception as e:
        logger.warning(
            f"[rr-code-synth] set_code_synth_running failed for "
            f"{scan_id}/{arxiv_id}: {type(e).__name__}: {e}"
        )
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def set_code_synth_error(
    scan_id: str, arxiv_id: str, prompt_version: str, message: str,
) -> None:
    """Mark a Build-tab generation as failed so the poll endpoint returns
    the error (and the frontend shows a Retry button) instead of leaving
    the operator stuck on "pending" forever."""
    payload = json.dumps({"status": "error", "message": message, "ts": time.time()})
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.set(
            keys.code_synth_status_key(scan_id, arxiv_id, prompt_version),
            payload, ex=params.CODE_SYNTH_STATUS_TTL_S,
        )
    except Exception as e:
        logger.warning(
            f"[rr-code-synth] set_code_synth_error failed for "
            f"{scan_id}/{arxiv_id}: {type(e).__name__}: {e}"
        )
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def get_code_synth_status(
    scan_id: str, arxiv_id: str, prompt_version: str,
) -> dict[str, Any] | None:
    """Read the in-flight/error status for one Build-tab generation.
    Returns None when never started, already succeeded (cleared on
    success — the MinIO cache is the source of truth for "done"), or on
    a Redis error (caller falls back to treating it as idle)."""
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        raw = await r.get(keys.code_synth_status_key(scan_id, arxiv_id, prompt_version))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning(
            f"[rr-code-synth] get_code_synth_status failed for "
            f"{scan_id}/{arxiv_id}: {type(e).__name__}: {e}"
        )
        return None
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def clear_code_synth_status(scan_id: str, arxiv_id: str, prompt_version: str) -> None:
    """DEL the status key on success — the MinIO cache written right
    before this call is now the source of truth."""
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.delete(keys.code_synth_status_key(scan_id, arxiv_id, prompt_version))
    except Exception:
        pass
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def _replay_snapshot(
    r: redis_aio.Redis, scan_id: str,
) -> list[dict]:
    """Per-scan snapshot list for late-subscriber catch-up. Empty on no history."""
    try:
        raw = await r.lrange(keys.snapshot_key(scan_id), 0, -1)
    except Exception:
        return []
    events: list[dict] = []
    for item in raw or []:
        try:
            events.append(json.loads(item))
        except Exception:
            continue
    return events


async def subscribe_events(
    scan_id: str,
    *,
    replay: bool = True,
) -> AsyncIterator[dict]:
    """Async iterator over phase events. Yields catch-up replay first,
    then live events. Caller owns close (SSE cancel path)."""
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(keys.event_channel(scan_id))
        if replay:
            for event in await _replay_snapshot(r, scan_id):
                yield event
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages = True,
                timeout = params.SSE_POLL_INTERVAL_S,
            )
            if msg is None:
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                yield json.loads(data)
            except Exception:
                continue
    except asyncio.CancelledError:
        return
    finally:
        try:
            await pubsub.unsubscribe(keys.event_channel(scan_id))
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Extraction cache — Redis cache for deep_read's per-paper 5-field output
# ---------------------------------------------------------------------------

async def _extraction_redis() -> redis_aio.Redis | None:
    """Lazy Redis client — None on env-misconfig so callers fall back
    gracefully (write_extraction proceeds without caching; prefill
    returns 0 hits)."""
    import os
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
    rds = await _extraction_redis()
    if rds is None:
        return None
    try:
        raw = await rds.get(keys.extraction_cache_key(arxiv_id))
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
    rds = await _extraction_redis()
    if rds is None:
        return False
    try:
        await rds.set(
            keys.extraction_cache_key(arxiv_id),
            json.dumps(extraction, default=str),
            ex = params.EXTRACTION_TTL_S,
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
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — synchronous fs_tool call from Celery
            # worker not inside the agent's asyncio.run scope. Use a
            # private loop.
            return asyncio.run(set_extraction(arxiv_id, extraction))
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
    # Deferred import — avoids a circular at module-load time (agent →
    # runtime for other things; this direction only needed at call time,
    # long after both packages have fully imported).
    from .. import agent

    if not top_n:
        return []
    rds = await _extraction_redis()
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
                raw = await rds.get(keys.extraction_cache_key(aid))
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
                agent.tools.state.fs_write(
                    scan_id, agent.keys.fs_extraction_path(aid), data,
                )
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


# ---------------------------------------------------------------------------
# fs mirror — Redis read-side mirror of the agent's per-scan virtual fs
# ---------------------------------------------------------------------------

def mirror_write_sync(scan_id: str, path: str, value: Any) -> None:
    """Best-effort sync mirror — called from inside the @tool body which
    runs in the Celery worker's asyncio loop. Sync Redis client because
    the tool may not be awaitable in all subagent contexts."""
    if not scan_id or not path:
        return
    import redis as redis_sync
    try:
        r = redis_sync.from_url(
            keys.redis_url(),
            socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = params.REDIS_OP_TIMEOUT_S,
        )
        try:
            payload = json.dumps(value, default=str)
            pipe = r.pipeline(transaction=False)
            pipe.set(keys.fs_mirror_key(scan_id, path), payload, ex=params.FS_MIRROR_TTL_S)
            pipe.sadd(keys.fs_mirror_index_key(scan_id), path)
            pipe.expire(keys.fs_mirror_index_key(scan_id), params.FS_MIRROR_TTL_S)
            pipe.execute()
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[rr-fs-mirror] sync write failed for {scan_id!r}/{path!r}: "
            f"{type(e).__name__}: {e}"
        )


async def mirror_read(scan_id: str, path: str) -> Any | None:
    """Read one fs entry. Returns the parsed JSON value or None on miss /
    Redis error. Used by `GET /scan/{id}/fs/{path}` to power the
    Pipeline-page drawer."""
    if not scan_id or not path:
        return None
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        raw = await r.get(keys.fs_mirror_key(scan_id, path))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode() if isinstance(raw, bytes) else raw
    except Exception as e:
        logger.warning(
            f"[rr-fs-mirror] read failed for {scan_id!r}/{path!r}: "
            f"{type(e).__name__}: {e}"
        )
        return None
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def mirror_index(scan_id: str) -> list[str]:
    """List every fs path mirrored for this scan. Returns [] on miss /
    error. Used by the drawer to discover what's available before
    fetching individual entries."""
    if not scan_id:
        return []
    r = redis_aio.from_url(
        keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = params.REDIS_OP_TIMEOUT_S,
    )
    try:
        raw = await r.smembers(keys.fs_mirror_index_key(scan_id))
        return sorted(p.decode() if isinstance(p, bytes) else str(p) for p in raw)
    except Exception as e:
        logger.warning(
            f"[rr-fs-mirror] index read failed for {scan_id!r}: "
            f"{type(e).__name__}: {e}"
        )
        return []
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Resilient single-LLM-call helper — for callers outside the DeepAgents loop
# ---------------------------------------------------------------------------

async def capture_llm_usage(response: object) -> None:
    """Bump the per-scan Redis counters straight from a returned
    `AIMessage` — no-ops silently when no scan is in context (e.g.
    code_synth's router endpoint, which doesn't set one) or when the
    response carries no usage block."""
    try:
        scan_id = llm_counter.service.get_scan()
        if not scan_id:
            return
        usage = getattr(response, "usage_metadata", None) or {}
        tokens_in  = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        if not (tokens_in or tokens_out):
            return
        meta = getattr(response, "response_metadata", None) or {}
        model = meta.get("model_name") or meta.get("model") or "unknown"
        llm_counter.service.bump_llm_usage(
            scan_id    = scan_id,
            phase      = llm_counter.service.get_phase(),
            model      = model,
            tokens_in  = tokens_in,
            tokens_out = tokens_out,
        )
    except Exception as e:
        logger.warning(f"[rr:llm_call] usage capture failed: {type(e).__name__}: {e}")


async def resilient_ainvoke(
    chain: Any,
    messages: Any,
    *,
    operation:    str,
    timeout_s:    float,
    max_attempts: int               = 3,
    backoff_s:    tuple[float, ...] = (2.0, 5.0),
):
    """`await asyncio.wait_for(chain.ainvoke(messages), timeout_s)` with
    transient-only retries inside the bound. Raises the last exception
    unchanged when attempts run out (or immediately for non-transient).

    Mirrors `domains.ycs.rag.service` (used by every YCS Ask node)."""
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                chain.ainvoke(messages),
                timeout = timeout_s,
            )
            # 2026-09-17: these calls run outside the DeepAgents loop, so
            # `RRLlmCounterCallback`'s per-turn timing log never sees
            # them — log here instead, same shape, so a slow backfill/
            # code_synth call is diagnosable from `kubectl logs` alone.
            logger.info(
                f"[rr:llm_call:{operation}] duration_s="
                f"{time.monotonic() - t0:.1f} attempt={attempt}/{max_attempts}"
            )
            await capture_llm_usage(result)
            return result
        except Exception as e:  # noqa: BLE001 — classified below
            last_exc = e
            duration_s = time.monotonic() - t0
            if not domain.is_transient(e) or attempt >= max_attempts:
                logger.warning(
                    f"[rr:llm_call:{operation}] FAILED after "
                    f"duration_s={duration_s:.1f} (attempt {attempt}/"
                    f"{max_attempts}, non-retryable or budget exhausted): "
                    f"{type(e).__name__}: {e}"
                )
                break
            delay = backoff_s[min(attempt - 1, len(backoff_s) - 1)]
            delay *= 1.0 + random.random() * 0.2
            logger.warning(
                f"[rr:llm_call:{operation}] transient "
                f"{type(e).__name__} after duration_s={duration_s:.1f} "
                f"(attempt {attempt}/{max_attempts}) — retrying in "
                f"{delay:.1f}s",
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
