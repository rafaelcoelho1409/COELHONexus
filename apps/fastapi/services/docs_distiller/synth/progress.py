"""Synth progress channel — Redis pub/sub + SSE bridge.

Mirrors planner/progress.py. Channel: `dd:synth:{thread_id}:events`.
Snapshot list: `dd:synth:{thread_id}:events:snapshot` (TTL 1h, max 200
events) so a late SSE subscriber catches up before live events resume.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

import redis.asyncio as redis_aio

from .cancel import _redis_url


logger = logging.getLogger(__name__)


_SNAPSHOT_TTL_S = 3600
_SNAPSHOT_MAX_EVENTS = 200


def _channel(thread_id: str) -> str:
    return f"dd:synth:{thread_id}:events"


def _snapshot_key(thread_id: str) -> str:
    return f"dd:synth:{thread_id}:events:snapshot"


async def emit_progress(
    thread_id: str,
    step: str,
    kind: str,
    **fields,
) -> None:
    """Publish one progress event. step = node name; kind = subtype
    ('start', 'sample', 'usc_voted', 'repair', 'done', 'error', ...).
    Best-effort — a Redis hiccup MUST NOT sink the node's work."""
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
        key = _snapshot_key(thread_id)
        pipe = r.pipeline(transaction=False)
        pipe.rpush(key, payload)
        pipe.ltrim(key, -_SNAPSHOT_MAX_EVENTS, -1)
        pipe.expire(key, _SNAPSHOT_TTL_S)
        await pipe.execute()
    except Exception as e:
        logger.warning(
            f"[synth-progress] emit failed for {thread_id} step={step!r} "
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
