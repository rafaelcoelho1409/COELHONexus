"""Pipeline cancel flag + watcher, per thread (planner or synth, by its id); TTL 1h since runs can take 20-30 min on large corpora. The flag mechanics live in `infra.redis.service`; this binds them to the thread's cancel key."""
from __future__ import annotations
import domains, infra

import asyncio


async def request_cancel(r: infra.redis.service.Redis, thread_id: str) -> None:
    await infra.redis.service.set_flag(
        r, domains.dd.pipeline.runtime.keys.cancel_key(thread_id), domains.dd.pipeline.runtime.params.CANCEL_TTL_S,
        error_label = "[pipeline-cancel] set failed",
    )


async def is_cancelled(r: infra.redis.service.Redis, thread_id: str) -> bool:
    return await infra.redis.service.flag_is_set(r, domains.dd.pipeline.runtime.keys.cancel_key(thread_id))


async def clear_cancel(r: infra.redis.service.Redis, thread_id: str) -> None:
    await infra.redis.service.clear_flag(r, domains.dd.pipeline.runtime.keys.cancel_key(thread_id))


async def reset(thread_id: str) -> None:
    """Clear the thread's cancel flag before its run starts (opens its own Redis client, for callers that must not import `infra`)."""
    async with infra.redis.service.client() as r:
        await clear_cancel(r, thread_id)


async def requested(thread_id: str) -> bool:
    """Whether the thread's cancel flag is set; False when Redis cannot be read (a run is never stopped by a Redis blip)."""
    try:
        async with infra.redis.service.client() as r:
            return await is_cancelled(r, thread_id)
    except Exception:
        return False


async def watcher(
    thread_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = 1.0,
) -> None:
    """Polls the cancel flag every `poll_interval_s` and cancels `main_task`
    on first True. Exits when main task completes or is cancelled."""
    await infra.redis.service.watch_flag(
        domains.dd.pipeline.runtime.keys.cancel_key(thread_id), main_task,
        poll_interval_s  = poll_interval_s,
        detected_message = (
            f"[pipeline-cancel] flag detected for thread "
            f"{thread_id} → cancelling main task"
        ),
        error_label      = "[pipeline-cancel] watcher Redis error",
    )
