"""Redis pub/sub for RR live phase events + task-id store for cancel.

Pattern mirrors `domains/dd/planner/runtime/progress/service.py`:

  emit_event(scan_id, phase, **fields)
      Publish ONE phase event. Best-effort: a Redis hiccup MUST NOT
      sink an in-progress scan.

  subscribe_events(scan_id) -> async iterator
      SSE side — subscribes + replays a TTL'd snapshot list so a late
      subscriber catches up on phases that already passed.

  store_task_id / get_task_id (sync + async pair)
      The Celery task UUID is held in a TTL'd Redis STRING keyed on
      scan_id so `POST /scan/{id}/cancel` can resolve scan_id →
      task_id and call `AsyncResult.revoke(terminate=True)`. TTL =
      24h covers any realistic in-flight scan + a grace window for
      post-mortem inspection.

Event shape:
  {
    "phase":  "running" | "discovery" | "triage" | "deep_read" |
              "graph_build" | "synthesis" | "report" | "persisting" |
              "done" | "error" | "cancelled",
    "ts":     float (unix seconds, UTC),
    "scan_id": str,
    ... arbitrary kwargs merged in (message, summary, ...)
  }
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

import redis.asyncio as redis_aio

from .keys import event_channel, redis_url, snapshot_key, task_id_key
from .observability import record_phase_event
from .params import (
    REDIS_CONNECT_TIMEOUT_S,
    REDIS_OP_TIMEOUT_S,
    SNAPSHOT_MAX_EVENTS,
    SNAPSHOT_TTL_S,
    SSE_POLL_INTERVAL_S,
    TASK_ID_TTL_S,
)


logger = logging.getLogger(__name__)


# Sync emit — for the Celery task body (Celery runs sync, brokers events to
# Redis without blocking the agent loop's own coroutine).
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
    record_phase_event(phase = phase)
    import redis as redis_sync
    try:
        r = redis_sync.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = REDIS_OP_TIMEOUT_S,
        )
        try:
            r.publish(event_channel(scan_id), payload)
            key = snapshot_key(scan_id)
            pipe = r.pipeline(transaction=False)
            pipe.rpush(key, payload)
            pipe.ltrim(key, -SNAPSHOT_MAX_EVENTS, -1)
            pipe.expire(key, SNAPSHOT_TTL_S)
            pipe.execute()
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[rr-events] emit_event_sync failed for {scan_id} phase={phase!r}: "
            f"{type(e).__name__}: {e}"
        )


# Async emit — for in-FastAPI emission paths (currently unused but keeps
# parity with the planner shape for future-proofing).
async def emit_event(scan_id: str, phase: str, **fields) -> None:
    """Async publisher. Same payload shape as emit_event_sync."""
    event = {
        "scan_id": scan_id,
        "phase":   phase,
        "ts":      time.time(),
        **fields,
    }
    payload = json.dumps(event, default=str)
    record_phase_event(phase = phase)
    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.publish(event_channel(scan_id), payload)
        key = snapshot_key(scan_id)
        pipe = r.pipeline(transaction=False)
        pipe.rpush(key, payload)
        pipe.ltrim(key, -SNAPSHOT_MAX_EVENTS, -1)
        pipe.expire(key, SNAPSHOT_TTL_S)
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


# Task-id store — scan_id → Celery task UUID. Written by `POST /scan` so
# `POST /scan/{id}/cancel` can revoke the right Celery task. Async (FastAPI
# request handler is async; no sync caller today). Best-effort: failure to
# warning since the task_id can't be resolved.
async def store_task_id(scan_id: str, task_id: str) -> None:
    """SET `rr:{scan_id}:task_id = task_id` with `TASK_ID_TTL_S` expiry."""
    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.set(task_id_key(scan_id), task_id, ex=TASK_ID_TTL_S)
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
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = REDIS_OP_TIMEOUT_S,
    )
    try:
        raw = await r.get(task_id_key(scan_id))
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
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.delete(task_id_key(scan_id))
    except Exception:
        pass
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


# Snapshot replay + subscribe — driven by the SSE route on the FastAPI side
async def _replay_snapshot(
    r: redis_aio.Redis, scan_id: str,
) -> list[dict]:
    """Per-scan snapshot list for late-subscriber catch-up. Empty on no history."""
    try:
        raw = await r.lrange(snapshot_key(scan_id), 0, -1)
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
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = REDIS_OP_TIMEOUT_S,
    )
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(event_channel(scan_id))
        if replay:
            for event in await _replay_snapshot(r, scan_id):
                yield event
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages = True,
                timeout = SSE_POLL_INTERVAL_S,
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
            await pubsub.unsubscribe(event_channel(scan_id))
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass
