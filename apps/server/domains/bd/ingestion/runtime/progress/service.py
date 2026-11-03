"""A run's progress record, its event stream and the per-book single-flight lock (the cancel flag lives in `../cancel/`). The stream
and lock mechanics live in `infra.redis.service`; this binds them to the book's keys."""
from __future__ import annotations
import domains, infra
from . import domain, keys, params

import json
import logging
import time
from collections.abc import AsyncIterator


logger = logging.getLogger(__name__)


async def emit(book_id: str, kind: str, **fields) -> None:
    """Publish an event and append it to the book's snapshot list, so a subscriber that connects late replays the history first."""
    await infra.redis.service.emit_event(
        keys.event_channel(book_id),
        keys.snapshot_key(book_id),
        {"kind": kind, "ts": time.time(), **fields},
        snapshot_max_events = params.SNAPSHOT_MAX_EVENTS,
        snapshot_ttl_s = params.SNAPSHOT_TTL_S,
        error_label = f"[bd-progress] emit failed for {book_id} kind={kind!r}",
    )


def subscribe(book_id: str, *, replay: bool = True, poll_interval_s: float = 0.5) -> AsyncIterator[dict]:
    """Async iterator over the book's events: the replay first, then live ones. The caller owns closing it (the SSE cancel path)."""
    return infra.redis.service.subscribe_events(
        keys.event_channel(book_id), keys.snapshot_key(book_id), replay = replay, poll_interval_s = poll_interval_s,
    )


async def clear_events(r: infra.redis.service.Redis, book_id: str) -> None:
    """Forget the last run's history before a new run starts, so the replay is of this run only."""
    await r.delete(keys.snapshot_key(book_id))


async def write_state(r: infra.redis.service.Redis, book_id: str, state: dict) -> None:
    try:
        await r.set(keys.progress_key(book_id), json.dumps({**state, "updated_at": time.time()}), ex = params.TTL_S)
    except Exception as e:  # noqa: BLE001 — a progress write never fails the run
        logger.info(f"[bd-progress] write skipped: {e}")


async def read_state(r: infra.redis.service.Redis, book_id: str) -> dict | None:
    try:
        raw = await r.get(keys.progress_key(book_id))
    except Exception:  # noqa: BLE001 — "no progress" is the answer when Redis cannot be read
        return None
    return domains.bd.ingestion.storage.domain.parse_json(raw, None)


async def acquire_lock(r: infra.redis.service.Redis, book_id: str, run_id: str, ttl_s: int = params.LOCK_QUEUED_S) -> tuple[bool, str | None]:
    """`(True, run_id)` when this run now holds the book's lock, else `(False, the run that does)`."""
    return await infra.redis.service.try_lock(r, keys.lock_key(book_id), run_id, ttl_s)


async def renew_lock(r: infra.redis.service.Redis, book_id: str, run_id: str) -> bool:
    """Keep this run's lock alive for another LOCK_LEASE_S: extend it while it is still ours, or take it again when it lapsed
    (a stall longer than the lease) and nobody else has it. False when another run holds it now."""
    try:
        if await infra.redis.service.extend_if_owner(r, keys.lock_key(book_id), run_id, params.LOCK_LEASE_S):
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[bd-lock] renew failed: {e}")
        return False
    taken, _ = await acquire_lock(r, book_id, run_id, params.LOCK_LEASE_S)
    return taken


async def read_lock(r: infra.redis.service.Redis, book_id: str) -> str | None:
    """The run holding the book's lock, or None."""
    try:
        holder = await r.get(keys.lock_key(book_id))
    except Exception:  # noqa: BLE001 — an unreadable lock is read as no lock
        return None
    if not holder:
        return None
    return holder.decode() if isinstance(holder, bytes) else holder


async def release_lock(r: infra.redis.service.Redis, book_id: str, run_id: str) -> bool:
    """Release iff this run is the holder."""
    try:
        return await infra.redis.service.release_if_owner(r, keys.lock_key(book_id), run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[bd-lock] release failed: {e}")
        return False


async def list_active(r: infra.redis.service.Redis) -> list[dict]:
    """In-flight extractions: the lock is held AND the progress record says queued / running."""
    active: list[dict] = []
    async for book_id, run_id in infra.redis.service.iter_held(r, keys.lock_prefix()):
        progress = await read_state(r, book_id)
        if domain.is_active(progress):
            active.append({"book_id": book_id, "run_id": run_id, "progress": progress})
    return active
