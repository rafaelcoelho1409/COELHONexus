"""Pipeline progress events — Redis pub/sub for mid-node events (LangGraph checkpoints only fire between nodes, so this fills the gap for the UI); snapshot list at the thread's `…:events:snapshot` so late SSE subscribers catch up before live. The stream mechanics live in `infra.redis.service`; this binds them to the thread's keys (planner or synth, by its id)."""
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
    """Publish a progress event and append to per-thread snapshot list so late SSE subscribers can replay catch-up history."""
    await infra.redis.service.emit_event(
        domains.dd.pipeline.runtime.keys.event_channel(thread_id),
        domains.dd.pipeline.runtime.keys.snapshot_key(thread_id),
        {"step": step, "kind": kind, "ts": time.time(), **fields},
        snapshot_max_events = domains.dd.pipeline.runtime.params.SNAPSHOT_MAX_EVENTS,
        snapshot_ttl_s      = domains.dd.pipeline.runtime.params.snapshot_ttl_s(thread_id),
        error_label         = (
            f"[{domains.dd.pipeline.runtime.keys.stage_of(thread_id)}-progress] emit failed for {thread_id} step={step!r} kind={kind!r}"
        ),
    )


def subscribe_progress(
    thread_id: str,
    *,
    replay: bool = True,
    poll_interval_s: float = 0.5,
) -> AsyncIterator[dict]:
    """Async iterator over progress events. Yields catch-up replay first,
    then live events. Caller owns close (SSE cancel path)."""
    return infra.redis.service.subscribe_events(
        domains.dd.pipeline.runtime.keys.event_channel(thread_id),
        domains.dd.pipeline.runtime.keys.snapshot_key(thread_id),
        replay = replay,
        poll_interval_s = poll_interval_s,
    )
