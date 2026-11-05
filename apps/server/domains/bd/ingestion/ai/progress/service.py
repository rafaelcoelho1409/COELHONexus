"""Replayable live events for Book Distiller agentic ingestion runs."""
from __future__ import annotations
import infra
from . import keys, params

import time
from collections.abc import AsyncIterator
from typing import Any


async def emit(book_id: str, agentic_run_id: str, kind: str, **fields: Any) -> None:
    await infra.redis.service.emit_event(
        keys.event_channel(book_id, agentic_run_id),
        keys.snapshot_key(book_id, agentic_run_id),
        {"kind": kind, "ts": time.time(), **fields},
        snapshot_max_events = params.SNAPSHOT_MAX_EVENTS,
        snapshot_ttl_s = params.SNAPSHOT_TTL_S,
        error_label = f"[bd-agentic-progress] emit failed for {book_id}/{agentic_run_id} kind={kind!r}",
    )


async def clear_events(book_id: str, agentic_run_id: str) -> None:
    """Drop the previous attempt's replay before resuming this run ID."""
    async with infra.redis.service.client() as redis:
        await redis.delete(keys.snapshot_key(book_id, agentic_run_id))


async def request_cancel(book_id: str, agentic_run_id: str) -> None:
    async with infra.redis.service.client() as redis:
        await redis.set(keys.cancel_key(book_id, agentic_run_id), "1", ex = params.SNAPSHOT_TTL_S)


async def cancel_requested(redis: infra.redis.service.Redis, book_id: str, agentic_run_id: str) -> bool:
    return await infra.redis.service.flag_is_set(redis, keys.cancel_key(book_id, agentic_run_id))


async def clear_cancel(book_id: str, agentic_run_id: str) -> None:
    async with infra.redis.service.client() as redis:
        await redis.delete(keys.cancel_key(book_id, agentic_run_id))


def subscribe(
    book_id: str,
    agentic_run_id: str,
    *,
    replay: bool = True,
    poll_interval_s: float = 0.5,
) -> AsyncIterator[dict]:
    return infra.redis.service.subscribe_events(
        keys.event_channel(book_id, agentic_run_id),
        keys.snapshot_key(book_id, agentic_run_id),
        replay = replay,
        poll_interval_s = poll_interval_s,
    )
