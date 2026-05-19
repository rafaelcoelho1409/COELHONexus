"""Synth cancel — Redis flag + asyncio.Task watcher. Mirrors planner/cancel.

Per-thread cancel flag at `dd:synth:{thread_id}:cancel`. The watcher
polls every 1s and cancels the main task on first True. Used by the
router's /synth/{thread_id}/cancel endpoint.
"""
from __future__ import annotations

import asyncio
import logging
import os

import redis.asyncio as redis_aio


logger = logging.getLogger(__name__)


_CANCEL_TTL_S = 3600


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "")
    return (
        f"redis://:{password}@{host}:{port}"
        if password else f"redis://{host}:{port}"
    )


def _cancel_key(thread_id: str) -> str:
    return f"dd:synth:{thread_id}:cancel"


async def request_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.set(_cancel_key(thread_id), "1", ex=_CANCEL_TTL_S)
    except Exception as e:
        logger.warning(f"[synth-cancel] set failed: {e}")


async def is_cancelled(r: redis_aio.Redis, thread_id: str) -> bool:
    try:
        v = await r.get(_cancel_key(thread_id))
    except Exception:
        return False
    return bool(v)


async def clear_cancel(r: redis_aio.Redis, thread_id: str) -> None:
    try:
        await r.delete(_cancel_key(thread_id))
    except Exception:
        pass


async def watcher(
    thread_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = 1.0,
) -> None:
    """Background task: poll cancel flag, cancel main_task on first True."""
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        while not main_task.done():
            try:
                if await is_cancelled(r, thread_id):
                    logger.info(
                        f"[synth-cancel] flag detected for thread "
                        f"{thread_id} → cancelling main task"
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
