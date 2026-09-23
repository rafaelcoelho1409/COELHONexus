"""Cancel flag I/O + watcher task; per-thread flag at coelhonexus:dd:synth:{thread_id}:cancel, polls 1s, cancels main task on first True."""
from __future__ import annotations
import domains

import asyncio
import logging

import redis.asyncio as redis_aio



logger = logging.getLogger(__name__)


async def request_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.set(domains.dd.synth.keys.cancel_key(thread_id), "1", ex = domains.dd.synth.params.CANCEL_TTL_S)
    except Exception as e:
        logger.warning(f"[synth-cancel] set failed: {e}")


async def is_cancelled(r: redis_aio.Redis, thread_id: str) -> bool:
    try:
        v = await r.get(domains.dd.synth.keys.cancel_key(thread_id))
    except Exception:
        return False
    return bool(v)


async def clear_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.delete(domains.dd.synth.keys.cancel_key(thread_id))
    except Exception:
        pass


async def watcher(
    thread_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = 1.0,
) -> None:
    """Poll cancel flag; cancel main_task on first True."""
    r = redis_aio.from_url(
        domains.dd.synth.keys.redis_url(),
        socket_connect_timeout = domains.dd.synth.params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = domains.dd.synth.params.REDIS_OP_TIMEOUT_S,
    )
    logger.info(f"[synth-cancel] watcher started for thread {thread_id}")
    try:
        while not main_task.done():
            try:
                if await is_cancelled(r, thread_id):
                    logger.info(
                        f"[synth-cancel] flag detected for thread "
                        f"{thread_id} → cancelling main task (issue #22: "
                        f"logged here so a slow-to-land cancel is "
                        f"distinguishable from a watcher that never ran)"
                    )
                    main_task.cancel()
                    return
            except Exception as e:
                logger.warning(f"[synth-cancel] watcher Redis error: {e}")
            await asyncio.sleep(poll_interval_s)
    except asyncio.CancelledError:
        return
    finally:
        try:
            await r.aclose()
        except Exception:
            pass
