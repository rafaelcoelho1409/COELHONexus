"""Planner cancel flag + watcher; TTL 1h since planner runs can take 20-30 min on large corpora. The flag mechanics live in `infra.redis.service`; this binds them to the planner's cancel key."""
from __future__ import annotations
import domains, infra

import asyncio


async def request_cancel(r: infra.redis.service.Redis, thread_id: str) -> None:
    await infra.redis.service.set_flag(
        r, domains.dd.planner.keys.cancel_key(thread_id), domains.dd.planner.params.CANCEL_TTL_S,
        error_label = "[planner-cancel] set failed",
    )


async def is_cancelled(r: infra.redis.service.Redis, thread_id: str) -> bool:
    return await infra.redis.service.flag_is_set(r, domains.dd.planner.keys.cancel_key(thread_id))


async def clear_cancel(r: infra.redis.service.Redis, thread_id: str) -> None:
    await infra.redis.service.clear_flag(r, domains.dd.planner.keys.cancel_key(thread_id))


async def watcher(
    thread_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = 1.0,
) -> None:
    """Polls the cancel flag every `poll_interval_s` and cancels `main_task`
    on first True. Exits when main task completes or is cancelled."""
    await infra.redis.service.watch_flag(
        domains.dd.planner.keys.cancel_key(thread_id), main_task,
        poll_interval_s  = poll_interval_s,
        detected_message = (
            f"[planner-cancel] flag detected for thread "
            f"{thread_id} → cancelling main task"
        ),
        error_label      = "[planner-cancel] watcher Redis error",
    )
