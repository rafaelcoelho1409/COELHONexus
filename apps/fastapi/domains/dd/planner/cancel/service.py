"""Cancel flag I/O + watcher task.

POST `/planner/{thread_id}/cancel` sets the Redis flag; the watcher inside
the running planner task polls it every 1s → `main_task.cancel()` →
LangGraph propagates CancelledError → POST returns `status="cancelled"`.
TTL 1h since a real run can take 20-30 min on a LangChain-scale corpus.
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as redis_aio

from ..keys import cancel_key, redis_url
from ..params import (
    CANCEL_TTL_S,
    REDIS_CONNECT_TIMEOUT_S,
    REDIS_OP_TIMEOUT_S,
)


logger = logging.getLogger(__name__)


async def request_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.set(cancel_key(thread_id), "1", ex = CANCEL_TTL_S)
    except Exception as e:
        logger.warning(f"[planner-cancel] set failed: {e}")


async def is_cancelled(r: redis_aio.Redis, thread_id: str) -> bool:
    try:
        v = await r.get(cancel_key(thread_id))
    except Exception:
        return False
    return bool(v)


async def clear_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.delete(cancel_key(thread_id))
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
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = REDIS_OP_TIMEOUT_S,
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
