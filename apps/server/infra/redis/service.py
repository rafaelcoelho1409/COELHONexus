"""Redis client factories.

The only module that imports `redis` or reads `REDIS_*` env vars. Everything
else reaches Redis dotted through here: `client()` / `client_sync()` (a client
for the duration of a `with` block), `new_redis()` / `new_redis_sync()` (a client
the caller owns and closes, for the rare long-lived case), `url()` (broker URL
for Celery), and the `Redis` / `Lock` classes for annotations and distributed
locks.

Clients are deliberately not cached: each caller opens one, uses it, and
closes it — FastAPI request handlers, Celery tasks (a fresh event loop per
task) and sync helpers all follow that lifecycle. `client()` is that lifecycle
in one line: `async with infra.redis.service.client() as r: ...`."""
from __future__ import annotations
from . import domain, params

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import redis as redis_sync
from redis.asyncio import Redis, from_url as _async_from_url
from redis.asyncio.lock import Lock


logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """True when `REDIS_HOST` is set."""
    return bool(params.HOST)


def url() -> str:
    """Connection URL built from the `REDIS_*` env vars."""
    if not params.HOST:
        raise RuntimeError("[redis] REDIS_HOST is not set")
    return domain.build_url(params.HOST, params.PORT, params.PASSWORD)


def new_redis(
    *, connect_timeout_s: float | None = params.CONNECT_TIMEOUT_S,
    op_timeout_s: float | None = params.OP_TIMEOUT_S, **kwargs: Any,
) -> Redis:
    """Fresh async client; the caller must `await aclose()` it."""
    return _async_from_url(
        url(), socket_connect_timeout = connect_timeout_s, socket_timeout = op_timeout_s, **kwargs,
    )


def new_redis_sync(
    *, connect_timeout_s: float | None = params.CONNECT_TIMEOUT_S,
    op_timeout_s: float | None = params.OP_TIMEOUT_S, **kwargs: Any,
) -> redis_sync.Redis:
    """Fresh blocking client; the caller must `close()` it."""
    return redis_sync.from_url(
        url(), socket_connect_timeout = connect_timeout_s, socket_timeout = op_timeout_s, **kwargs,
    )


@contextlib.asynccontextmanager
async def client(**kwargs: Any) -> AsyncIterator[Redis]:
    """`async with client() as r:` — a fresh async client, closed on exit (a failing close is swallowed). A missing
    `REDIS_HOST` raises on entry, like `new_redis()`; `kwargs` are `new_redis`'s timeout overrides."""
    r = new_redis(**kwargs)
    try:
        yield r
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


@contextlib.contextmanager
def client_sync(**kwargs: Any) -> Iterator[redis_sync.Redis]:
    """Blocking twin of `client()`."""
    r = new_redis_sync(**kwargs)
    try:
        yield r
    finally:
        try:
            r.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Locks — a key holding the owner id; release only by the owner.
# ---------------------------------------------------------------------------

async def release_if_owner(r: Redis, key: str, owner: str) -> bool:
    """Delete `key` only while it still holds `owner` (atomic compare-and-delete). True when deleted."""
    return bool(await r.eval(params.RELEASE_IF_OWNER_LUA, 1, key, owner))


def release_if_owner_sync(r: redis_sync.Redis, key: str, owner: str) -> bool:
    """Blocking-client twin of `release_if_owner`."""
    return bool(r.eval(params.RELEASE_IF_OWNER_LUA, 1, key, owner))


async def try_lock(r: Redis, key: str, owner: str, ttl_s: int) -> tuple[bool, str | None]:
    """`SET key owner NX EX ttl_s`: `(True, owner)` when this call took the lock, else `(False, current holder)` — the
    holder is None when it expired between the two reads."""
    if await r.set(key, owner, nx = True, ex = ttl_s):
        return True, owner
    existing = await r.get(key)
    return False, (_text(existing) if existing else None)


async def iter_held(r: Redis, prefix: str, *, skip: str | None = None) -> AsyncIterator[tuple[str, str]]:
    """`(suffix, value)` of each key under `prefix` that holds a value, found with SCAN (never a blocking KEYS).
    `skip` leaves out the key whose suffix equals it — "any lock but mine"."""
    cursor = 0
    while True:
        cursor, found = await r.scan(cursor = cursor, match = f"{prefix}*", count = 100)
        for raw_key in found:
            key = _text(raw_key)
            suffix = key[len(prefix):]
            if skip is not None and suffix == skip:
                continue
            value = await r.get(key)
            if value is None:
                continue
            yield suffix, _text(value)
        if cursor == 0:
            return


async def first_held(r: Redis, prefix: str, *, skip: str | None = None) -> tuple[str, str] | None:
    """The first `iter_held` entry, or None when nothing under `prefix` holds a value."""
    held = iter_held(r, prefix, skip = skip)
    try:
        async for item in held:
            return item
    finally:
        await held.aclose()
    return None


async def delete_matching(r: Redis, match: str, *, batch_size: int = 500) -> int:
    """Delete every key matching the glob `match` (SCAN + batched DEL); returns how many were deleted."""
    deleted = 0
    batch: list = []
    async for key in r.scan_iter(match = match, count = batch_size):
        batch.append(key)
        if len(batch) >= batch_size:
            deleted += await r.delete(*batch)
            batch = []
    if batch:
        deleted += await r.delete(*batch)
    return deleted


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else value


# ---------------------------------------------------------------------------
# Event stream — pub/sub channel + a bounded replay snapshot list. A producer
# publishes live and appends to the snapshot, so a late SSE subscriber can replay
# the history before following the live events.
# ---------------------------------------------------------------------------

async def emit_event(
    channel: str, snapshot_key: str, event: Mapping[str, Any], *,
    snapshot_max_events: int, snapshot_ttl_s: int, error_label: str,
) -> None:
    """Publish `event` (JSON) on `channel` and append it to the capped, expiring list at
    `snapshot_key`. Best-effort: a Redis failure is logged as `<error_label>: <error>` and
    never raised, so a hiccup cannot sink the work being reported."""
    payload = json.dumps(event, default = str)
    async with client() as r:
        try:
            await r.publish(channel, payload)
            pipe = r.pipeline(transaction = False)
            pipe.rpush(snapshot_key, payload)
            pipe.ltrim(snapshot_key, -snapshot_max_events, -1)
            pipe.expire(snapshot_key, snapshot_ttl_s)
            await pipe.execute()
        except Exception as e:
            logger.warning(f"{error_label}: {type(e).__name__}: {e}")


async def replay_snapshot(r: Redis, snapshot_key: str) -> list[dict]:
    """Events in the snapshot list, oldest first. Empty on no history or on error."""
    try:
        raw = await r.lrange(snapshot_key, 0, -1)
    except Exception:
        return []
    events: list[dict] = []
    for item in raw or []:
        try:
            events.append(json.loads(item))
        except Exception:
            continue
    return events


async def subscribe_events(
    channel: str, snapshot_key: str, *, replay: bool = True, poll_interval_s: float = 0.5,
) -> AsyncIterator[dict]:
    """Async iterator over a channel's events: the snapshot replay first (when `replay`), then
    live events. The caller owns closing it (SSE cancel path)."""
    async with client() as r:
        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(channel)
            if replay:
                for event in await replay_snapshot(r, snapshot_key):
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
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Flags — a key that means "yes" while it exists (cooperative cancel), plus a watcher
# that cancels an asyncio task as soon as its flag appears.
# ---------------------------------------------------------------------------

async def set_flag(r: Redis, key: str, ttl_s: int, *, error_label: str) -> None:
    """Raise the flag `key` for `ttl_s` seconds. A Redis failure is logged, never raised."""
    try:
        await r.set(key, "1", ex = ttl_s)
    except Exception as e:
        logger.warning(f"{error_label}: {e}")


async def flag_is_set(r: Redis, key: str) -> bool:
    """True while the flag exists; False when absent or when Redis fails."""
    try:
        v = await r.get(key)
    except Exception:
        return False
    return bool(v)


async def clear_flag(r: Redis, key: str) -> None:
    try:
        await r.delete(key)
    except Exception:
        pass


async def watch_flag(
    key: str, main_task: asyncio.Task, *, poll_interval_s: float = 1.0,
    detected_message: str, error_label: str, started_message: str | None = None,
) -> None:
    """Poll `key` every `poll_interval_s`; cancel `main_task` on the first hit. Exits when the
    task finishes or the watcher itself is cancelled."""
    async with client() as r:
        if started_message:
            logger.info(started_message)
        try:
            while not main_task.done():
                try:
                    if await flag_is_set(r, key):
                        logger.info(detected_message)
                        main_task.cancel()
                        return
                except Exception as e:
                    logger.warning(f"{error_label}: {e}")
                await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            return


# Public surface for the rest of the code (the re-exports are intentional).
__all__ = [
    "Lock", "Redis", "clear_flag", "client", "client_sync", "delete_matching", "emit_event", "first_held",
    "flag_is_set", "is_configured", "iter_held", "new_redis", "new_redis_sync", "release_if_owner",
    "release_if_owner_sync", "replay_snapshot", "set_flag", "subscribe_events", "try_lock", "url", "watch_flag",
]
