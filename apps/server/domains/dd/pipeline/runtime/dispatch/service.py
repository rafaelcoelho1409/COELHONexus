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
        synth = await redis.first_held(r, domains.dd.pipeline.runtime.keys.synth_lock_prefix())
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


# ---------------------------------------------------------------------------
# Synth: what the API does around enqueueing a run (a whole study or one chapter) — single-flight lock, cross-stage block, study registry, cancel
# propagation, wipe.
# ---------------------------------------------------------------------------

async def reserve_synth_start(slug: str, thread_id: str) -> dict | None:
    """Single-flight gate for starting a synth run (a whole study or one chapter) on `slug`. Returns the `locked` response when a planner is running
    (planner and synth share the LLM resources), another slug's synth is, or this slug's synth lock is held; else None, with the slug's lock taken for
    `thread_id` and its cancel flag cleared — the caller then enqueues the run and calls `release_synth_start` if that fails."""
    redis = infra.redis.service
    keys = domains.dd.pipeline.runtime.keys
    async with redis.client() as r:
        planner = await redis.first_held(r, keys.lock_prefix())
        if planner is not None:
            planner_slug, planner_thread = planner
            return {
                "status": "locked",
                "slug": planner_slug,
                "thread_id": planner_thread,
                "stage": "planner",
                "message": (
                    f"A planner is running ({planner_slug!r}, thread_id={planner_thread}). Planner and Synth share the same LLM resources — running "
                    f"both at once degrades quality on each. Wait for the planner to finish or cancel it before starting a synth."
                ),
            }
        other = await redis.first_held(r, keys.synth_lock_prefix(), skip = slug)
        if other is not None:
            other_slug, other_thread = other
            return {
                "status": "locked",
                "slug": other_slug,
                "thread_id": other_thread,
                "stage": "synth",
                "message": (
                    f"Another synth is running ({other_slug!r}, thread_id={other_thread}). Wait for it to finish or cancel it before starting {slug!r}."
                ),
            }
        acquired, existing = await redis.try_lock(r, keys.synth_lock_key(slug), thread_id, domains.dd.pipeline.runtime.params.SYNTH_LOCK_TTL_S)
        if not acquired:
            return {
                "status": "locked",
                "slug": slug,
                "thread_id": existing,
                "stage": "synth",
                "message": f"A synth of {slug!r} is already running (thread_id={existing}). Wait for it to finish or cancel it before retrying.",
            }
        await domains.dd.pipeline.runtime.cancel.service.clear_cancel(r, thread_id)
    return None


async def release_synth_start(slug: str) -> None:
    """Drop the slug's synth lock after a failed enqueue (best-effort: never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.delete(domains.dd.pipeline.runtime.keys.synth_lock_key(slug))
    except Exception:
        pass


async def register_active_study(slug: str, study_thread_id: str) -> None:
    """Record the live study so a page refresh can reconnect to it (best-effort: logged, never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.set(
                domains.dd.pipeline.runtime.keys.active_study_key(slug),
                json.dumps({"study_thread_id": study_thread_id, "started_ts": time.time()}),
                ex = domains.dd.pipeline.runtime.params.ACTIVE_STUDY_TTL_S,
            )
    except Exception as e:
        logger.warning(f"[synth-study] {slug}: active-run register failed: {type(e).__name__}: {e}")


async def track_chapter(study_thread_id: str, chapter_thread_id: str) -> None:
    """Add a chapter thread to its study's in-flight set, so a cancel of the study reaches it (best-effort: logged, never raised)."""
    try:
        async with infra.redis.service.client() as r:
            chapters_key = domains.dd.pipeline.runtime.keys.active_chapters_key(study_thread_id)
            await r.sadd(chapters_key, chapter_thread_id)
            await r.expire(chapters_key, domains.dd.pipeline.runtime.params.ACTIVE_CHAPTERS_TTL_S)
    except Exception as e:
        logger.warning(f"[synth-study] active_chapters SADD failed for {chapter_thread_id!r}: {e}")


async def untrack_chapter(study_thread_id: str, chapter_thread_id: str) -> None:
    """Drop a finished chapter thread from its study's in-flight set (best-effort: never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.srem(domains.dd.pipeline.runtime.keys.active_chapters_key(study_thread_id), chapter_thread_id)
    except Exception:
        pass


async def clear_active_study(slug: str) -> None:
    """Best-effort delete of the study live-run registry key (logged, never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.delete(domains.dd.pipeline.runtime.keys.active_study_key(slug))
    except Exception as e:
        logger.warning(f"[synth-study] {slug}: active-run clear failed: {type(e).__name__}: {e}")


async def read_active_synth(slug: str) -> dict:
    """Page-refresh recovery: the live study's thread id, or — for a single-chapter run, which has no study registry entry — the chapter thread holding the
    slug's synth lock (`chapter_thread_id`); `{"active": False}` when neither (also when Redis is unreachable)."""
    keys = domains.dd.pipeline.runtime.keys
    chapter_tid = None
    try:
        async with infra.redis.service.client() as r:
            sid = await r.get(keys.active_study_key(slug))
            if not sid:
                held = await r.get(keys.synth_lock_key(slug))
                if isinstance(held, (bytes, bytearray)):
                    held = held.decode("utf-8", "replace")
                if held and not str(held).startswith(f"{domains.dd.pipeline.runtime.dispatch.keys.STUDY_THREAD_PREFIX}/"):
                    chapter_tid = str(held)
    except Exception:
        return {"active": False}
    if chapter_tid:
        return {"active": True, "chapter_thread_id": chapter_tid, "study_thread_id": None, "started_ts": None}
    if not sid:
        return {"active": False}
    if isinstance(sid, (bytes, bytearray)):
        sid = sid.decode("utf-8", "replace")
    try:
        data = json.loads(sid)
        return {"active": True, "study_thread_id": data.get("study_thread_id"), "started_ts": data.get("started_ts")}
    except Exception:
        return {"active": True, "study_thread_id": str(sid), "started_ts": None}


async def synth_in_flight(slug: str) -> bool:
    """True while a synth run holds the slug: a study registry entry, or — for a single-chapter run — the slug's synth lock. Raises when Redis is
    unreachable; the caller decides what that means."""
    keys = domains.dd.pipeline.runtime.keys
    async with infra.redis.service.client() as r:
        running = await r.get(keys.active_study_key(slug))
        held = await r.get(keys.synth_lock_key(slug)) if not running else None
    return bool(running or held)


async def synth_active_lock() -> dict | None:
    """The synth run holding a lock right now — `{"slug", "thread_id"}` — or None."""
    async with infra.redis.service.client() as r:
        held = await infra.redis.service.first_held(r, domains.dd.pipeline.runtime.keys.synth_lock_prefix())
    return None if held is None else {"slug": held[0], "thread_id": held[1]}


async def cancel_synth_run(thread_id: str) -> list[str]:
    """Raise the thread's cancel flag. A STUDY thread's cancel must propagate to each in-flight per-chapter thread — chapter watchers poll their own flag,
    not the study flag; without propagation only the NEXT chapter is blocked and the in-flight one keeps firing LLM calls. Returns the chapter threads
    it propagated to."""
    cancel = domains.dd.pipeline.runtime.cancel.service
    keys = domains.dd.pipeline.runtime.keys
    propagated_to: list[str] = []
    async with infra.redis.service.client() as r:
        await cancel.request_cancel(r, thread_id)
        parts = thread_id.split("/")
        if len(parts) >= 4 and parts[1] == "study":
            slug = parts[2]
            seen: set[str] = set()
            # SADDs before spawn → catches chapters that just started but haven't emitted a progress event yet (invisible to the snapshot scan).
            try:
                members = await r.smembers(keys.active_chapters_key(thread_id))
                for raw in members or []:
                    ch_tid = raw.decode() if isinstance(raw, bytes) else raw
                    if ch_tid and ch_tid not in seen:
                        await cancel.request_cancel(r, ch_tid)
                        propagated_to.append(ch_tid)
                        seen.add(ch_tid)
            except Exception as e:
                logger.warning(f"[cancel_synth] active_chapters read failed for {thread_id!r}: {type(e).__name__}: {e}")
            # Belt and suspenders: scan the progress snapshots. Covers a chapter that emitted progress but never landed in the set. Idempotent: de-duped via `seen`.
            scan_pattern = keys.synth_snapshot_pattern(f"{domains.dd.pipeline.runtime.dispatch.keys.CHAPTER_THREAD_PREFIX}/{slug}/")
            try:
                async for key in r.scan_iter(match = scan_pattern, count = 200):
                    if isinstance(key, bytes):
                        key = key.decode()
                    ch_tid = keys.thread_id_from_snapshot_key(key)
                    if ch_tid in seen:
                        continue
                    await cancel.request_cancel(r, ch_tid)
                    propagated_to.append(ch_tid)
                    seen.add(ch_tid)
            except Exception as e:
                logger.warning(f"[cancel_synth] scan/propagate failed for {thread_id!r}: {type(e).__name__}: {e}")

    progress = domains.dd.pipeline.runtime.progress.service
    await progress.emit_progress(thread_id, "synth", "cancel_requested")
    for ch_tid in propagated_to:
        try:
            await progress.emit_progress(ch_tid, "synth", "cancel_requested")
        except Exception:
            pass
    logger.info(f"[cancel_synth] {thread_id}: flag set; propagated to {len(propagated_to)} chapter thread(s)")
    return propagated_to


async def wipe_synth_redis(slug: str) -> int:
    """Delete the slug's SSE snapshots, thread state, study registry and lock — without the sweep a wiped slug "comes back from the dead" via the cached
    study SSE snapshot. Returns how many keys went (-1 when Redis fails — logged)."""
    keys = domains.dd.pipeline.runtime.keys
    prefixes = (domains.dd.pipeline.runtime.dispatch.keys.CHAPTER_THREAD_PREFIX, domains.dd.pipeline.runtime.dispatch.keys.STUDY_THREAD_PREFIX)
    n_redis = 0
    try:
        async with infra.redis.service.client() as r:
            for prefix in prefixes:
                n_redis += await infra.redis.service.delete_matching(r, keys.synth_thread_state_pattern(f"{prefix}/{slug}/"))
            n_redis += await r.delete(keys.active_study_key(slug), keys.synth_lock_key(slug))
    except Exception as e:
        logger.warning(f"[synth-wipe] Redis delete failed for {slug!r}: {e}")
        return -1
    return n_redis
