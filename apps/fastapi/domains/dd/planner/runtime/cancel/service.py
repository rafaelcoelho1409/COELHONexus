"""Cancel flag I/O + watcher; TTL 1h since planner runs can take 20-30 min on large corpora."""
from __future__ import annotations
import domains

import asyncio
import logging

import redis.asyncio as redis_aio



logger = logging.getLogger(__name__)


async def request_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.set(domains.dd.planner.keys.cancel_key(thread_id), "1", ex = domains.dd.planner.params.CANCEL_TTL_S)
    except Exception as e:
        logger.warning(f"[planner-cancel] set failed: {e}")


async def is_cancelled(r: redis_aio.Redis, thread_id: str) -> bool:
    try:
        v = await r.get(domains.dd.planner.keys.cancel_key(thread_id))
    except Exception:
        return False
    return bool(v)


async def clear_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.delete(domains.dd.planner.keys.cancel_key(thread_id))
    except Exception:
        pass


async def watcher(
    thread_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = 1.0,
) -> None:
    """Polls the cancel flag every `poll_interval_s` and cancels `main_task`
    on first True. Exits when main task completes or is cancelled."""
    r = redis_aio.from_url(
        domains.dd.planner.keys.redis_url(),
        socket_connect_timeout = domains.dd.planner.params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = domains.dd.planner.params.REDIS_OP_TIMEOUT_S,
    )
    try:
        while not main_task.done():
            try:
                if await is_cancelled(r, thread_id):
                    logger.info(
                        f"[planner-cancel] flag detected for thread "
                        f"{thread_id} → cancelling main task"
                    )
                    main_task.cancel()
                    return
            except Exception as e:
                logger.warning(f"[planner-cancel] watcher Redis error: {e}")
            await asyncio.sleep(poll_interval_s)
    except asyncio.CancelledError:
        return
    finally:
        try:
            await r.aclose()
        except Exception:
            pass
