"""Redis pub/sub for sub-node progress events (LangGraph's native checkpoint
fires only between nodes; this surfaces mid-node events live to the UI).

  emit_progress(thread_id, step, kind, **fields)
      Publish ONE event; best-effort, Redis hiccup must NOT sink real work.
  subscribe_progress(thread_id) -> async iterator
      SSE side: subscribes + replays a TTL'd snapshot list so a late
      subscriber catches up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

import redis.asyncio as redis_aio

from ...keys import event_channel, redis_url, snapshot_key
from ...params import (
    REDIS_CONNECT_TIMEOUT_S,
    REDIS_OP_TIMEOUT_S,
    SNAPSHOT_MAX_EVENTS,
    SNAPSHOT_TTL_S,
)


logger = logging.getLogger(__name__)


async def emit_progress(
    thread_id: str,
    step: str,
    kind: str,
    **fields,
) -> None:
    """`step` = planner node name, `kind` = event subtype
    (start/batch/llm_call/done/error). Extra fields merge as-is.
    Appends to a per-thread Redis list (capped at SNAPSHOT_MAX_EVENTS) so
    a late SSE subscriber replays catch-up history before live events."""
    event = {
        "step": step,
        "kind": kind,
        "ts":   time.time(),
        **fields,
    }
    payload = json.dumps(event, default = str)

    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = REDIS_OP_TIMEOUT_S,
    )
    try:
        await r.publish(event_channel(thread_id), payload)
        key = snapshot_key(thread_id)
        pipe = r.pipeline(transaction = False)
        pipe.rpush(key, payload)
        pipe.ltrim(key, -SNAPSHOT_MAX_EVENTS, -1)
        pipe.expire(key, SNAPSHOT_TTL_S)
        await pipe.execute()
    except Exception as e:
        logger.warning(
            f"[planner-progress] emit failed for {thread_id} step={step!r} "
            f"kind={kind!r}: {type(e).__name__}: {e}"
        )
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def _replay_snapshot(
    r: redis_aio.Redis, thread_id: str,
) -> list[dict]:
    """Per-thread snapshot list for late SSE catch-up. Empty on no history."""
    try:
        raw = await r.lrange(snapshot_key(thread_id), 0, -1)
    except Exception:
        return []
    events: list[dict] = []
    for item in raw or []:
        try:
            events.append(json.loads(item))
        except Exception:
            continue
    return events


async def subscribe_progress(
    thread_id: str,
    *,
    replay: bool = True,
    poll_interval_s: float = 0.5,
) -> AsyncIterator[dict]:
    """Async iterator over progress events. Yields catch-up replay first,
    then live events. Caller owns close (SSE cancel path)."""
    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = REDIS_OP_TIMEOUT_S,
    )
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(event_channel(thread_id))
        if replay:
            for event in await _replay_snapshot(r, thread_id):
                yield event
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages = True,
                timeout = poll_interval_s,
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
            await pubsub.unsubscribe(event_channel(thread_id))
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass
