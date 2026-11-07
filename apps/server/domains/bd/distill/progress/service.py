"""Replayable progress and cooperative cancellation for Distill runs."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import infra

from . import keys, params


async def emit(book_id: str, run_id: str, kind: str, **fields: Any) -> None:
    await infra.redis.service.emit_event(
        keys.channel(book_id, run_id),
        keys.snapshot(book_id, run_id),
        {"kind": kind, **fields},
        snapshot_max_events = params.SNAPSHOT_MAX_EVENTS,
        snapshot_ttl_s = params.SNAPSHOT_TTL_S,
        error_label = f"[bd-distill-progress] emit failed for {book_id}/{run_id}",
    )


async def clear(book_id: str, run_id: str) -> None:
    async with infra.redis.service.client() as redis:
        await redis.delete(keys.snapshot(book_id, run_id), keys.cancel(book_id, run_id))


async def clear_cancel(book_id: str, run_id: str) -> None:
    async with infra.redis.service.client() as redis:
        await redis.delete(keys.cancel(book_id, run_id))


async def request_cancel(book_id: str, run_id: str) -> None:
    async with infra.redis.service.client() as redis:
        await infra.redis.service.set_flag(
            redis,
            keys.cancel(book_id, run_id),
            params.SNAPSHOT_TTL_S,
            error_label = f"[bd-distill-progress] cancel flag failed for {book_id}/{run_id}",
        )


async def cancel_requested(redis: infra.redis.service.Redis, book_id: str, run_id: str) -> bool:
    return await infra.redis.service.flag_is_set(redis, keys.cancel(book_id, run_id))


def subscribe(
    book_id: str,
    run_id: str,
    *,
    replay: bool = True,
    poll_interval_s: float = 0.5,
) -> AsyncIterator[dict]:
    return infra.redis.service.subscribe_events(
        keys.channel(book_id, run_id), keys.snapshot(book_id, run_id),
        replay = replay, poll_interval_s = poll_interval_s,
    )
