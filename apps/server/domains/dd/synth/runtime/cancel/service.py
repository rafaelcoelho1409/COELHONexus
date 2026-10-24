"""Synth cancel flag + watcher task; per-thread flag at coelhonexus:dd:synth:{thread_id}:cancel, polls 1s, cancels main task on first True. The flag mechanics live in `infra.redis.service`; this binds them to the synth's cancel key."""
from __future__ import annotations
import domains, infra

import asyncio


async def request_cancel(r: infra.redis.service.Redis, thread_id: str) -> None:
    await infra.redis.service.set_flag(
        r, domains.dd.synth.keys.cancel_key(thread_id), domains.dd.synth.params.CANCEL_TTL_S,
        error_label = "[synth-cancel] set failed",
    )


async def is_cancelled(r: infra.redis.service.Redis, thread_id: str) -> bool:
    return await infra.redis.service.flag_is_set(r, domains.dd.synth.keys.cancel_key(thread_id))


async def clear_cancel(r: infra.redis.service.Redis, thread_id: str) -> None:
    await infra.redis.service.clear_flag(r, domains.dd.synth.keys.cancel_key(thread_id))


async def watcher(
    thread_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = 1.0,
) -> None:
    """Poll cancel flag; cancel main_task on first True."""
    await infra.redis.service.watch_flag(
        domains.dd.synth.keys.cancel_key(thread_id), main_task,
        poll_interval_s  = poll_interval_s,
        started_message  = f"[synth-cancel] watcher started for thread {thread_id}",
        detected_message = (
            f"[synth-cancel] flag detected for thread "
            f"{thread_id} → cancelling main task (issue #22: "
            f"logged here so a slow-to-land cancel is "
            f"distinguishable from a watcher that never ran)"
        ),
        error_label      = "[synth-cancel] watcher Redis error",
    )
