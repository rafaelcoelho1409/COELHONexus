"""Cancel is cooperative (progress.raise_if_cancelled + watcher pre-empts blocking awaits). The framework's lock is a short lease the
watcher keeps renewing while the run is alive, so a task that dies frees the framework within `progress.params.LOCK_LEASE_S`."""
from __future__ import annotations
import domains, infra
from . import domain, params
from ... import tiers

import asyncio
import logging
import time
import uuid


logger = logging.getLogger(__name__)


_TIER_MODULES = {
    "llms_full": tiers.tier1,
    "llms_txt":  tiers.tier2,
    "sitemap":   tiers.tier3,
    "docs":      tiers.tier4,
    "github":    tiers.tier5,
}


async def run_tier(kind: str, url: str, *, entry: dict, slug: str, progress, store, **extra) -> int:
    """Run the tier of source `kind` on `url`, handing it what the catalog `entry` gives that tier (`domain.tier_arguments`) and
    any `extra` arguments. The one place a source kind becomes a tier call: the dispatcher and the debug route both use it."""
    return await _TIER_MODULES[kind].service.run(
        url = url, framework_slug = slug, progress = progress, store = store,
        **domain.tier_arguments(kind, entry), **extra,
    )


async def run_source_chain(chain, progress, *, new_progress, run_tier, base_result: dict, slug: str):
    """Run the first source in `chain` (`[{kind, url}]`, best first) that works, and return the `Progress` the winning
    tier used. A tier that says its source is unusable — a link index instead of content, a page of links with none to
    follow, a 404 or an HTML page — hands over to the next declared source with a fresh `Progress`; any other error
    ends the run as before. When nothing is left the run fails, naming every reason."""
    reasons: list[str] = []
    for position, source in enumerate(chain):
        kind, url = source["kind"], source["url"]
        base_result["tier_kind"] = kind
        base_result["tier_url"] = url
        try:
            await run_tier(kind, url, progress)
            return progress
        except tiers.errors.FALL_THROUGH as unusable:
            reasons.append(f"{kind} ({url}): {unusable}")
            if isinstance(unusable, tiers.errors.EmptyLinksDetected):
                # Tier 2 does not log its own downgrade (tier 1 does): without this marker the progress row would sit
                # at "running" and the UI trail would lose the fact that a fallback happened at all.
                await progress.record_url(
                    url, status = "downgrade", tier = kind, error_msg = "zero links, falling through",
                )
            await progress.finish(status = "downgrade")
            following = chain[position + 1] if position + 1 < len(chain) else None
            if following is None:
                break
            logger.info(
                f"[dispatch] {slug}: {kind} unusable ({type(unusable).__name__}) — "
                f"falling through to {following['kind']} ({following['url']})"
            )
            await progress.close()
            progress = new_progress()
    raise RuntimeError(f"no declared source for {slug!r} worked: " + "; ".join(reasons))


async def _cancel_watcher(
    redis_client: infra.redis.service.Redis,
    run_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = params.CANCEL_POLL_S,
    *,
    slug: str | None = None,
    renew_every_s: float | None = None,
) -> None:
    """Bypasses Progress throttle (calls is_cancelled() directly; sleep is the rate limit). Crawl4AI arun_many can block 30-60 s.
    Given the run's `slug` it is also the lock's heartbeat: every `renew_every_s` it renews the framework's lease, so the lock lives
    exactly as long as this loop does — a task that dies stops renewing and the lock lapses on its own."""
    if renew_every_s is None:
        renew_every_s = domains.dd.ingestion.runtime.progress.params.LOCK_RENEW_S
    renew_at = 0.0
    try:
        while not main_task.done():
            try:
                if await domains.dd.ingestion.runtime.cancel.service.is_cancelled(redis_client, run_id):
                    logger.info(
                        f"[dispatch] {run_id}: cancel flag detected by "
                        f"watcher → cancelling main task"
                    )
                    main_task.cancel()
                    return
                if slug is not None and time.monotonic() >= renew_at:
                    renew_at = time.monotonic() + renew_every_s
                    if not await domains.dd.ingestion.runtime.progress.service.renew_lock(redis_client, slug, run_id):
                        logger.warning(f"[dispatch] {slug}: the lock is held by another run now (run_id={run_id})")
            except Exception as e:  # noqa: BLE001 — the watcher must outlive a Redis hiccup
                logger.warning(f"[dispatch] cancel watcher Redis error: {e}")
            await asyncio.sleep(poll_interval_s)
    except asyncio.CancelledError:
        return


async def _cleanup_framework(minio, framework_slug: str) -> int:
    """Wipe `ingestion/{slug}/` so partial corpora aren't reused."""
    try:
        n = await minio.delete_prefix(domains.dd.ingestion.storage.keys.framework_prefix(framework_slug))
        logger.info(
            f"[dispatch] cleanup {framework_slug}: deleted {n} MinIO objects"
        )
        return n
    except Exception as e:
        logger.warning(f"[dispatch] cleanup {framework_slug} failed: {e}")
        return 0


async def _restore_old_copy(minio, slug: str) -> None:
    """After a failed or cancelled run: the copy it replaced (set aside when it started) goes back. Never raises — it runs in an
    error path and must not hide the error; a copy that cannot be put back is logged."""
    storage = domains.dd.ingestion.storage.service
    try:
        backup_id = await storage.latest_backup(minio, slug)
        if backup_id is None:
            return
        n = await storage.restore_old_copy(minio, slug, backup_id)
        if n:
            await storage.drop_old_copy(minio, slug)
        logger.info(f"[dispatch] {slug}: the run did not finish — the previous copy is back ({n} objects)")
    except Exception as e:  # noqa: BLE001 — runs in an error path and must not hide the error
        logger.warning(f"[dispatch] {slug}: could not put the previous copy back: {type(e).__name__}: {e}")


async def reserve_run(slug: str, refresh: bool) -> dict:
    """Single-flight gate for starting an ingestion of `slug`. Returns a terminal response — `locked` (another
    ingestion is running, this slug's lock is held, or a concurrent start won the lock) or `cached` (a manifest
    exists and no refresh was asked) — or `{"status": "reserved", "run_id": ...}` with the slug's lock held and the
    cancel flag cleared: the caller enqueues the run and calls `release_run` if that fails. A refresh does not touch the
    stored copy here: the run itself sets the old copy aside when it starts (`storage.service.stash_old_copy`) and puts it
    back if it fails or is cancelled."""
    progress = domains.dd.ingestion.runtime.progress.service
    async with infra.redis.service.client() as r:
        other = await progress.find_other_running(r, slug)
        if other is not None:
            running_slug, running_run_id = other
            return domain.locked_by_other(slug, running_slug, running_run_id)
        active = await progress.read_lock(r, slug)
        if active:
            return domain.locked_by_same(slug, active)

        if not refresh:
            minio = infra.minio.service.get_storage()
            cached = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
            if cached:
                return domain.cached(slug, cached)

        run_id = uuid.uuid4().hex
        if not await progress.acquire_lock(r, slug, run_id):
            return domain.locked_by_race(slug, await progress.read_lock(r, slug))
        await domains.dd.ingestion.runtime.cancel.service.clear_cancel(r, run_id)
    return domain.reserved(slug, run_id)


async def release_run(slug: str, run_id: str) -> None:
    """Give the slug's lock back after a failed enqueue (best-effort: never raised, so it cannot mask the failure)."""
    try:
        async with infra.redis.service.client() as r:
            await domains.dd.ingestion.runtime.progress.service.release_lock(r, slug, run_id)
    except Exception:
        pass


async def run(run_id: str, slug: str) -> dict:
    """Session + span + metrics wrapper around the ingestion dispatcher.
    The session wrapper was missing until now — every other DD/RR/YCS
    pipeline groups its spans into one LangFuse trace via `session(...)`;
    ingestion had the span but not the session, so its spans never
    grouped into one trace in the LangFuse UI.

    2026-09-24: the LangFuse `session_id` is a friendly
    `docs-distiller/{slug}/{run_id}` label (matches planner's
    `docs-distiller/{slug}/...` and synth's
    `docs-distiller/study/{slug}/...`) instead of the bare `run_id` —
    ingestion was the only DD stage findable in the LangFuse UI only by
    trace ID. `run_id` itself is untouched everywhere else (Redis
    locks/progress keys, MinIO manifest paths, the `/runs/{run_id}`
    REST routes all still use the plain uuid4 hex) — this only changes
    the cosmetic label LangFuse groups spans under."""
    t0 = asyncio.get_running_loop().time()
    with infra.langfuse.service.session(
        params.LANGFUSE_SESSION_NAME,
        session_id = domain.session_id(slug, run_id),
        user_id    = slug,
        framework  = slug,
    ), infra.opentelemetry.service.get_tracer().start_as_current_span(
        params.SPAN_NAME,
        attributes = {**domain.run_attributes(run_id, slug), infra.langfuse.keys.KEEP: True},
    ):
        result = await _run_inner(run_id, slug)
    domains.dd.ingestion.observability.service.record_ingestion_run(
        framework = slug,
        duration_s = max(asyncio.get_running_loop().time() - t0, 0.0),
        **domain.run_metrics(result),
    )
    return result


async def _run_inner(run_id: str, slug: str) -> dict:
    """Framework lock held by run_id on entry (acquired in POST /runs); released in finally."""
    catalog = domains.dd.resolver.service.index_by_slug()
    entry = catalog.get(slug)
    if entry is None:
        return domain.rejected_result(run_id, slug, f"unknown framework slug: {slug!r}")

    best = domain.pick_best(entry)
    if best is None:
        return domain.rejected_result(run_id, slug, "no source URLs in catalog entry", framework_name = entry["name"])

    progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
    r = infra.redis.service.new_redis(op_timeout_s = params.REDIS_OP_TIMEOUT_S)
    minio = infra.minio.service.get_storage()
    store = domains.dd.ingestion.storage.service.Store(run_id, slug, r, minio)

    base_result = domain.base_result(run_id, slug, entry, best)

    # Spawn watcher BEFORE the main try so it's active from the first await.
    watcher_task = asyncio.create_task(
        _cancel_watcher(r, run_id, asyncio.current_task(), slug = slug),
    )

    try:
        # The lock was taken when the run was queued, with the longer queued lease: this run now keeps it alive as it goes. If another
        # run holds it by now (this one waited longer than even that lease for a free worker), this one stands down without touching
        # the framework's data — the failure handlers below would wipe the other run's partial copy.
        if not await domains.dd.ingestion.runtime.progress.service.renew_lock(r, slug, run_id):
            return domain.rejected_result(
                run_id, slug, "the framework's lock was taken by another run while this one waited to start",
                framework_name = entry["name"],
            )

        await progress.raise_if_cancelled()

        # A re-ingestion sets the copy it replaces aside (and empties the slug's prefixes) before it writes anything; the copy
        # comes back if the run fails or is cancelled, and is dropped once the new one is finalized.
        backup_id = await domains.dd.ingestion.storage.service.stash_old_copy(minio, slug)
        if backup_id:
            logger.info(f"[dispatch] {slug}: the previous copy is set aside ({backup_id}) until this run is done")

        async def _chain_tier(kind: str, url: str, tier_progress) -> None:
            await run_tier(kind, url, entry = entry, slug = slug, progress = tier_progress, store = store)

        # The best declared source first; each lower one only if the one before turns out unusable today.
        progress = await run_source_chain(
            domain.source_chain(entry),
            progress,
            new_progress = lambda: domains.dd.ingestion.runtime.progress.service.Progress(run_id),
            run_tier = _chain_tier,
            base_result = base_result,
            slug = slug,
        )

        await progress.raise_if_cancelled()

        await progress.start(tier = "post", total = 0)

        post_summary = await domains.dd.ingestion.post.service.apply_to_store(store)
        await progress.record_post(**domain.post_record(base_result["tier_kind"], post_summary))

        await progress.raise_if_cancelled()

        # split_monolith/dedup_pages rewrite the manifest with fresh idx/keys
        # (post.service.apply_to_store) — the vaults Store.add_page built at
        # fetch time are keyed off the PRE-split idx/slug and are now orphaned;
        # every new entry has none. Idempotent (skips entries that already
        # have both vault blobs), so this is cheap even when nothing changed.
        # Own tier + real total/current — on a large framework (400+ pages)
        # this was previously a single multi-second block with the progress
        # UI stuck showing "post" and no counter moving at all.
        await progress.start(tier = "vault_backfill", total = post_summary["output_files"])

        async def _on_backfill_progress(done: int, total: int) -> None:
            await progress.update(current = done)

        try:
            await domains.dd.synth.nodes.backfill.service.backfill_vaults_for_framework(
                slug, on_progress = _on_backfill_progress,
            )
        except Exception as e:
            logger.warning(
                f"[dispatch] {slug}: post-ingest vault backfill failed: "
                f"{type(e).__name__}: {e}"
            )

        await progress.start(tier = "finalize", total = 0)

        await store.finalize(extra = domain.finalize_extra(entry, base_result, run_id, getattr(store, "index_summary", "")))

        await progress.finish(status = "done")

        try:
            await domains.dd.ingestion.storage.service.drop_old_copy(minio, slug)
        except Exception as e:  # noqa: BLE001 — the run is done; a backup that cannot be dropped is only logged
            logger.warning(f"[dispatch] {slug}: could not drop the previous copy's backup: {type(e).__name__}: {e}")

        return domain.done_result(base_result, store.manifest, post_summary)

    except (domains.dd.ingestion.runtime.progress.errors.IngestCancelled, asyncio.CancelledError):
        logger.info(f"[dispatch] {slug}: cancelled by user (run_id={run_id})")
        # CRITICAL: stop watcher BEFORE cleanup — poll loop fires a second main_task.cancel() mid-cleanup, raising CancelledError inside delete_prefix and leaving MinIO stragglers (observed: 428).
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass
        # Two-pass cleanup: in-flight MinIO writes complete after cancel but before gather unwinds; settle gap lets them finish, second sweep catches stragglers.
        n1 = await _cleanup_framework(minio, slug)
        await asyncio.sleep(params.CLEANUP_SETTLE_S)
        n2 = await _cleanup_framework(minio, slug)
        if n2 > 0:
            logger.info(
                f"[dispatch] {slug}: cleanup pass 2 caught {n2} straggler "
                f"objects (pass 1 deleted {n1})"
            )
        await _restore_old_copy(minio, slug)
        await progress.finish(status = "cancelled")
        return domain.cancelled_result(base_result)

    except Exception as e:
        logger.exception(f"[dispatch] {slug}: failed")
        # Wipe partial state so the next cached-check sees nothing — then the previous copy, if there was one, goes back.
        await _cleanup_framework(minio, slug)
        await _restore_old_copy(minio, slug)
        await progress.finish(status = "failed", error = domain.error_text(e))
        return domain.failed_result(base_result, e)

    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await domains.dd.ingestion.runtime.progress.service.release_lock(r, slug, run_id)
        except Exception:
            pass
        try:
            await store.close()
        except Exception:
            pass
        try:
            await progress.close()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass
