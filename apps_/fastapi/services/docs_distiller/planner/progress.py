"""Planner progress channel — Redis pub/sub + SSE bridge.

Real-time mid-node progress reporting for the docs-distiller planner.
LangGraph's native checkpointer only writes state between nodes; this
side channel surfaces sub-node events (embedding-batch progress,
LLM-judge progress, errors) live to the FastHTML UI via Server-Sent
Events (the 2026-canonical pattern for HTML-over-the-wire real-time
updates per HTMX docs).

Two halves:

  emit_progress(redis, thread_id, step, kind, **fields)
      Called from inside a planner node. Publishes a JSON event to
      Redis channel `dd:planner:{thread_id}:events`. Best-effort —
      a Redis hiccup must not sink the node's actual work.

  subscribe_progress(thread_id) -> async iterator
      Called by the SSE endpoint. Subscribes to the channel and
      yields each event as a Python dict. Also flushes a last-known
      snapshot (kept in a Redis key with a TTL) so a late SSE
      subscriber catches up rather than waiting for the next event.

Channel + snapshot keys are namespaced by thread_id so cross-run
events never leak.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

import redis.asyncio as redis_aio

from .cancel import _redis_url


logger = logging.getLogger(__name__)


_SNAPSHOT_TTL_S = 3600   # keep the last N events for ~1h post-run
_SNAPSHOT_MAX_EVENTS = 200


def _channel(thread_id: str) -> str:
    return f"dd:planner:{thread_id}:events"


def _snapshot_key(thread_id: str) -> str:
    return f"dd:planner:{thread_id}:events:snapshot"


async def emit_progress(
    thread_id: str,
    step: str,
    kind: str,
    **fields,
) -> None:
    """Publish ONE progress event. `step` is the planner node name
    ("embed_corpus", "off_topic", ...); `kind` is the event subtype
    ("start", "batch", "llm_call", "done", "error"). Extra fields
    are merged into the event payload as-is.

    Each call also appends the event to a per-thread Redis list (capped
    at _SNAPSHOT_MAX_EVENTS) so a late SSE subscriber can replay
    catch-up history before live events resume.
    """
    event = {
        "step": step,
        "kind": kind,
        "ts":   time.time(),
        **fields,
    }
    payload = json.dumps(event, default=str)

    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await r.publish(_channel(thread_id), payload)
        # Append to snapshot history (RPUSH + LTRIM keeps the last N).
        key = _snapshot_key(thread_id)
        pipe = r.pipeline(transaction=False)
        pipe.rpush(key, payload)
        pipe.ltrim(key, -_SNAPSHOT_MAX_EVENTS, -1)
        pipe.expire(key, _SNAPSHOT_TTL_S)
        await pipe.execute()
    except Exception as e:
        # Logging only — a side-channel failure must NEVER sink the
        # node's actual work.
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
    """Read the per-thread snapshot list so a late SSE subscriber
    catches up on whatever's already happened. Returns events in
    publish order; empty list on no history."""
    try:
        raw = await r.lrange(_snapshot_key(thread_id), 0, -1)
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
    """Async iterator over progress events for `thread_id`. Yields
    every event published to the per-thread channel, plus an optional
    catch-up replay of any events that landed before the subscriber
    arrived. Caller is responsible for closing the iterator (the
    StreamingResponse cancel path handles this in the SSE endpoint)."""
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(_channel(thread_id))
        if replay:
            for event in await _replay_snapshot(r, thread_id):
                yield event
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=poll_interval_s,
            )
            if msg is None:
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                yield json.loads(data)
            except Exception:
                # Bad payload — skip rather than crash the stream.
                continue
    except asyncio.CancelledError:
        return
    finally:
        try:
            await pubsub.unsubscribe(_channel(thread_id))
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass
