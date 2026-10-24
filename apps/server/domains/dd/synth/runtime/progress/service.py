"""Redis pub/sub for synth progress + SSE bridge; snapshot list at …:events:snapshot (TTL 24h, max 200) so late SSE subscribers catch up before live."""
from __future__ import annotations
import domains, infra

import asyncio
import json
import logging
import time
from typing import AsyncIterator


logger = logging.getLogger(__name__)


async def emit_progress(
    thread_id: str,
    step: str,
    kind: str,
    **fields,
) -> None:
    """Publish one event. step=node name; kind=subtype (start/sample/done/error).
    Best-effort — a Redis hiccup must NOT sink the node's work."""
    event = {
        "step": step,
        "kind": kind,
        "ts":   time.time(),
        **fields,
    }
    payload = json.dumps(event, default = str)

    r = infra.redis.service.new_redis()
    try:
        await r.publish(domains.dd.synth.keys.event_channel(thread_id), payload)
        key = domains.dd.synth.keys.snapshot_key(thread_id)
        pipe = r.pipeline(transaction = False)
        pipe.rpush(key, payload)
        pipe.ltrim(key, -domains.dd.synth.params.SNAPSHOT_MAX_EVENTS, -1)
        pipe.expire(key, domains.dd.synth.params.SNAPSHOT_TTL_S)
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
    r: infra.redis.service.Redis, thread_id: str,
) -> list[dict]:
    try:
        raw = await r.lrange(domains.dd.synth.keys.snapshot_key(thread_id), 0, -1)
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
    """Yield catch-up replay then live events. SSE owns close."""
    r = infra.redis.service.new_redis()
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(domains.dd.synth.keys.event_channel(thread_id))
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
            await pubsub.unsubscribe(domains.dd.synth.keys.event_channel(thread_id))
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass
