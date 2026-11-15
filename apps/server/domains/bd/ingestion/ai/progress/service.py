"""Replayable live events for Book Distiller agentic ingestion runs."""
from __future__ import annotations
import infra
from . import keys, params

import time
from collections.abc import AsyncIterator
from typing import Any


class RunExecutionLeaseLostError(RuntimeError):
    """Raised when a worker can no longer prove exclusive ownership of a graph run."""


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


async def acquire_run_execution_lock(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
    owner: str,
) -> tuple[bool, str | None]:
    """Claim the single-writer lease for one graph run."""
    return await infra.redis.service.try_lock(
        redis,
        keys.run_execution_lock_key(book_id, agentic_run_id),
        owner,
        params.RUN_EXECUTION_LOCK_LEASE_S,
    )


async def run_execution_lock_is_held(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
) -> bool:
    """Check whether a Celery worker still owns the single-writer lease for this run."""
    return bool(await redis.exists(keys.run_execution_lock_key(book_id, agentic_run_id)))


async def owns_run_execution_lock(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
    owner: str,
) -> bool:
    """Verify the exact worker token still owns the single-writer lease."""
    current_owner = await redis.get(keys.run_execution_lock_key(book_id, agentic_run_id))
    if isinstance(current_owner, bytes):
        current_owner = current_owner.decode()
    return current_owner == owner


async def renew_run_execution_lock(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
    owner: str,
) -> bool:
    """Renew only while this worker still owns the run lease; never take over a lease after losing it."""
    return await infra.redis.service.extend_if_owner(
        redis,
        keys.run_execution_lock_key(book_id, agentic_run_id),
        owner,
        params.RUN_EXECUTION_LOCK_LEASE_S,
    )


async def release_run_execution_lock(
    redis: infra.redis.service.Redis,
    book_id: str,
    agentic_run_id: str,
    owner: str,
) -> bool:
    """Release the single-writer lease only when it still belongs to this worker."""
    return await infra.redis.service.release_if_owner(
        redis,
        keys.run_execution_lock_key(book_id, agentic_run_id),
        owner,
    )


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
