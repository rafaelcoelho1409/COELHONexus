"""What the API does around enqueueing a planner run: single-flight lock, cross-stage block, live-run registry, cancel, wipe."""
from __future__ import annotations
import domains
import infra

import json
import logging
import time


logger = logging.getLogger(__name__)


async def reserve_start(slug: str, thread_id: str) -> dict | None:
    """Single-flight gate for starting a planner run on `slug`. Returns the `locked` response when a synth is running
    (planner and synth share the LLM resources), another slug's planner is, or this slug's planner lock is held; else
    None, with the slug's lock taken for `thread_id` and its cancel flag cleared — the caller then enqueues the run
    and calls `release_start` if that fails."""
    redis = infra.redis.service
    async with redis.client() as r:
        synth = await redis.first_held(r, domains.dd.pipeline.synth.keys.lock_prefix())
        if synth is not None:
            synth_slug, synth_thread = synth
            return {
                "status": "locked",
                "slug": synth_slug,
                "thread_id": synth_thread,
                "stage": "synth",
                "message": (
                    f"A synth is running ({synth_slug!r}, "
                    f"thread_id={synth_thread}). Planner and Synth "
                    f"share the same LLM resources — running both "
                    f"at once degrades quality on each. Wait for "
                    f"the synth to finish or cancel it before "
                    f"starting a planner."
                ),
            }
        other = await redis.first_held(r, domains.dd.pipeline.runtime.keys.lock_prefix(), skip=slug)
        if other is not None:
            other_slug, other_thread = other
            return {
                "status": "locked",
                "slug": other_slug,
                "thread_id": other_thread,
                "stage": "planner",
                "message": (
                    f"Another planner is running ({other_slug!r}, "
                    f"thread_id={other_thread}). Wait for it to "
                    f"finish or cancel it before starting {slug!r}."
                ),
            }
        acquired, existing = await redis.try_lock(
            r, domains.dd.pipeline.runtime.keys.lock_key(slug), thread_id, domains.dd.pipeline.runtime.params.LOCK_TTL_S,
        )
        if not acquired:
            return {
                "status": "locked",
                "slug": slug,
                "thread_id": existing,
                "stage": "planner",
                "message": (
                    f"A planner of {slug!r} is already running "
                    f"(thread_id={existing}). Wait for it to finish "
                    f"or cancel it before retrying."
                ),
            }
        await domains.dd.pipeline.runtime.cancel.service.clear_cancel(r, thread_id)
    return None


async def release_start(slug: str) -> None:
    """Drop the slug's planner lock after a failed enqueue (best-effort: never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.delete(domains.dd.pipeline.runtime.keys.lock_key(slug))
    except Exception:
        pass


async def register_active_run(slug: str, thread_id: str) -> None:
    """Record the live run so a page refresh can reconnect to it (best-effort: logged, never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.set(
                domains.dd.pipeline.runtime.keys.active_run_key(slug),
                json.dumps({"thread_id": thread_id, "started_ts": time.time()}),
                ex = domains.dd.pipeline.runtime.params.ACTIVE_RUN_TTL_S,
            )
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: active-run register failed: "
            f"{type(e).__name__}: {e}"
        )


async def clear_active_run(slug: str) -> None:
    """Best-effort delete of the planner live-run registry key."""
    try:
        async with infra.redis.service.client() as r:
            await r.delete(domains.dd.pipeline.runtime.keys.active_run_key(slug))
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: active-run clear failed "
            f"({type(e).__name__}: {e})"
        )


async def read_active_run(slug: str) -> dict:
    """Page-refresh recovery: `{"active": True, "thread_id", "started_ts"}` for the slug's live run, else
    `{"active": False}` (also when Redis is unreachable)."""
    try:
        async with infra.redis.service.client() as r:
            raw = await r.get(domains.dd.pipeline.runtime.keys.active_run_key(slug))
    except Exception:
        return {"active": False}
    if not raw:
        return {"active": False}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
        return {
            "active": True,
            "thread_id": data.get("thread_id"),
            "started_ts": data.get("started_ts"),
        }
    except Exception:
        return {"active": True, "thread_id": str(raw), "started_ts": None}


async def active_lock() -> dict | None:
    """The planner run holding a lock right now — `{"slug", "thread_id"}` — or None."""
    async with infra.redis.service.client() as r:
        held = await infra.redis.service.first_held(r, domains.dd.pipeline.runtime.keys.lock_prefix())
    return None if held is None else {"slug": held[0], "thread_id": held[1]}


async def cancel_run(thread_id: str) -> None:
    """Raise the thread's cancel flag and drop its slug's live-run registry entry."""
    async with infra.redis.service.client() as r:
        await domains.dd.pipeline.runtime.cancel.service.request_cancel(r, thread_id)
        if thread_id.count("/") >= 2:
            await r.delete(domains.dd.pipeline.runtime.keys.active_run_key(thread_id.split("/")[1]))


async def wipe_redis(slug: str) -> int:
    """Delete the slug's live-run registry and lock (so the next start is not rejected by a stale lock); returns how
    many keys went (0 when Redis fails — logged)."""
    try:
        async with infra.redis.service.client() as r:
            return await r.delete(
                domains.dd.pipeline.runtime.keys.active_run_key(slug),
                domains.dd.pipeline.runtime.keys.lock_key(slug),
            )
    except Exception as e:
        logger.warning(f"[planner-wipe] Redis delete failed for {slug!r}: {e}")
        return 0
