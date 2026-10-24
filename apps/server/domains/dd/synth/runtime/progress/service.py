"""Synth progress events — Redis pub/sub + SSE bridge; snapshot list at …:events:snapshot (TTL 24h, max 200) so late SSE subscribers catch up before live. The stream mechanics live in `infra.redis.service`; this binds them to the synth's keys."""
from __future__ import annotations
import domains, infra

import time
from collections.abc import AsyncIterator


async def emit_progress(
    thread_id: str,
    step: str,
    kind: str,
    **fields,
) -> None:
    """Publish one event. step=node name; kind=subtype (start/sample/done/error).
    Best-effort — a Redis hiccup must NOT sink the node's work."""
    await infra.redis.service.emit_event(
        domains.dd.synth.keys.event_channel(thread_id),
        domains.dd.synth.keys.snapshot_key(thread_id),
        {"step": step, "kind": kind, "ts": time.time(), **fields},
        snapshot_max_events = domains.dd.synth.params.SNAPSHOT_MAX_EVENTS,
        snapshot_ttl_s      = domains.dd.synth.params.SNAPSHOT_TTL_S,
        error_label         = (
            f"[synth-progress] emit failed for {thread_id} step={step!r} kind={kind!r}"
        ),
    )


def subscribe_progress(
    thread_id: str,
    *,
    replay: bool = True,
    poll_interval_s: float = 0.5,
) -> AsyncIterator[dict]:
    """Yield catch-up replay then live events. SSE owns close."""
    return infra.redis.service.subscribe_events(
        domains.dd.synth.keys.event_channel(thread_id),
        domains.dd.synth.keys.snapshot_key(thread_id),
        replay = replay,
        poll_interval_s = poll_interval_s,
    )
