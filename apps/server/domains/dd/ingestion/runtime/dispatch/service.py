"""Cancel is cooperative (progress.raise_if_cancelled + watcher pre-empts blocking awaits). Lock TTL (35 min) outlasts Celery soft_time_limit (30 min) so crashed tasks self-release."""
from __future__ import annotations
import domains, infra
from . import domain, params
from ... import tiers

import asyncio
import logging
import uuid
from dataclasses import asdict


logger = logging.getLogger(__name__)


_TIER_MODULES = {
    "llms_full": tiers.tier1,
    "llms_txt":  tiers.tier2,
    "sitemap":   tiers.tier3,
    "docs":      tiers.tier4,
    "github":    tiers.tier5,
}


_FALL_THROUGH = (
    tiers.errors.ManifestDetected,
    tiers.errors.EmptyLinksDetected,
    tiers.errors.TierUnavailable,
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
        except _FALL_THROUGH as unusable:
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
    redis_client: "infra.redis.service.Redis",
    run_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = params.CANCEL_POLL_S,
) -> None:
    """Bypasses Progress throttle (calls is_cancelled() directly; sleep is the rate limit). Crawl4AI arun_many can block 30-60 s."""
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
            except Exception as e:
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


async def reserve_run(slug: str, refresh: bool) -> dict:
    """Single-flight gate for starting an ingestion of `slug`. Returns a terminal response — `locked` (another
    ingestion is running, this slug's lock is held, or a concurrent start won the lock) or `cached` (a manifest
    exists and no refresh was asked) — or `{"status": "reserved", "run_id": ...}` with the slug's lock held and the
    cancel flag cleared: the caller enqueues the run and calls `release_run` if that fails. A refresh first wipes the
    slug's stale MinIO prefixes."""
    progress = domains.dd.ingestion.runtime.progress.service
    async with infra.redis.service.client() as r:
        other = await progress.find_other_running(r, slug)
        if other is not None:
            running_slug, running_run_id = other
            return {
                "status": "locked",
                "slug": running_slug,
                "run_id": running_run_id,
                "message": (
                    f"Another ingestion is running ({running_slug!r}, "
                    f"run_id={running_run_id}). Wait for it to finish or "
                    f"cancel it before starting {slug!r}."
                ),
            }
        active = await progress.read_lock(r, slug)
        if active:
            return {
                "status": "locked",
                "slug": slug,
                "run_id": active,
                "message": (
                    f"An ingestion of {slug!r} is already running "
                    f"(run_id={active}). Wait for it to finish or cancel "
                    f"it before triggering another."
                ),
            }

        minio = infra.minio.service.get_storage()
        if not refresh:
            cached = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
            if cached:
                return {"status": "cached", "slug": slug, "run_id": None, "manifest": cached}
        else:
            for prefix in (
                domains.dd.ingestion.storage.keys.framework_prefix(slug),
                f"ingestion-raw/{slug}/",
                f"synth-vault/{slug}/",
            ):
                try:
                    n = await minio.delete_prefix(prefix)
                    if n:
                        logger.info(
                            f"[runs] refresh wipe: deleted {n} stale objects "
                            f"from {prefix!r} before re-ingestion"
                        )
                except Exception as e:
                    logger.warning(f"[runs] refresh wipe failed for {prefix!r}: {e}")

        run_id = uuid.uuid4().hex
        if not await progress.acquire_lock(r, slug, run_id):
            held = await progress.read_lock(r, slug)
            return {
                "status": "locked",
                "slug": slug,
                "run_id": held or "?",
                "message": "Concurrent acquire race; try again.",
            }
        await domains.dd.ingestion.runtime.cancel.service.clear_cancel(r, run_id)
    return {"status": "reserved", "slug": slug, "run_id": run_id}


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
        "dd-ingestion",
        session_id = f"docs-distiller/{slug}/{run_id}",
        user_id    = slug,
        framework  = slug,
    ), infra.opentelemetry.service.get_tracer().start_as_current_span(
        "dd.ingestion.run",
        attributes = {
            "dd.domain":                "ingestion",
            "dd.run.kind":              "ingestion",
            "ingestion.run_id":         run_id,
            "ingestion.framework_slug": slug,
            infra.langfuse.keys.KEEP:     True,
        },
    ):
        result = await _run_inner(run_id, slug)
    post_summary = result.get("post") or {}
    domains.dd.ingestion.observability.service.record_ingestion_run(
        framework = slug,
        tier_kind = str(result.get("tier_kind") or "unknown"),
        outcome = str(result.get("status") or "unknown"),
        duration_s = max(asyncio.get_running_loop().time() - t0, 0.0),
        output_files = int(post_summary.get("output_files", 0) or 0),
        output_bytes = int(post_summary.get("output_bytes", 0) or 0),
    )
    return result


async def _run_inner(run_id: str, slug: str) -> dict:
    """Framework lock held by run_id on entry (acquired in POST /runs); released in finally."""
    catalog = domains.dd.resolver.service.index_by_slug()
    entry = catalog.get(slug)
    if entry is None:
        return {
            "run_id": run_id, "slug": slug, "status": "failed",
            "error": f"unknown framework slug: {slug!r}",
        }

    best = domain.pick_best(entry)
    if best is None:
        return {
            "run_id": run_id, "slug": slug,
            "framework_name": entry["name"],
            "status": "failed",
            "error": "no source URLs in catalog entry",
        }

    progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
    r = infra.redis.service.new_redis(op_timeout_s = params.REDIS_OP_TIMEOUT_S)
    minio = infra.minio.service.get_storage()
    store = domains.dd.ingestion.storage.service.Store(run_id, slug, r, minio)

    base_result = {
        "run_id": run_id,
        "slug": slug,
        "framework_name": entry["name"],
        "tier_kind": best["kind"],
        "tier_url": best["url"],
    }

    # Spawn watcher BEFORE the main try so it's active from the first await.
    watcher_task = asyncio.create_task(
        _cancel_watcher(r, run_id, asyncio.current_task()),
    )

    try:
        await progress.raise_if_cancelled()

        async def run_tier(kind: str, url: str, tier_progress) -> None:
            mod = _TIER_MODULES[kind]
            kwargs: dict = {
                "url": url, "framework_slug": slug,
                "progress": tier_progress, "store": store,
            }
            if mod in (domains.dd.ingestion.tiers.tier3, domains.dd.ingestion.tiers.tier4):
                kwargs["framework_name"] = entry["name"]
            if mod in (
                domains.dd.ingestion.tiers.tier1,
                domains.dd.ingestion.tiers.tier2,
                domains.dd.ingestion.tiers.tier3,
                domains.dd.ingestion.tiers.tier4,
            ):
                kwargs["path_filter"] = entry.get("path_filter")
            if mod is domains.dd.ingestion.tiers.tier1:
                kwargs["nav_url"] = entry.get("llms_txt")  # the site's own page order, for the split
            if mod is domains.dd.ingestion.tiers.tier2:
                kwargs["sitemap_url"] = entry.get("sitemap")  # to tell a curated index from the docs
            await mod.service.run(**kwargs)

        # The best declared source first; each lower one only if the one before turns out unusable today.
        progress = await run_source_chain(
            domain.source_chain(entry),
            progress,
            new_progress = lambda: domains.dd.ingestion.runtime.progress.service.Progress(run_id),
            run_tier = run_tier,
            base_result = base_result,
            slug = slug,
        )

        await progress.raise_if_cancelled()

        await progress.start(tier = "post", total = 0)

        post_summary = await domains.dd.ingestion.post.service.apply_to_store(store)
        await progress.record_post(
            tier = base_result["tier_kind"],
            input_files = post_summary["input_files"],
            input_bytes = post_summary["input_bytes"],
            output_files = post_summary["output_files"],
            output_bytes = post_summary["output_bytes"],
            was_split = post_summary["was_split"],
            stubs_dropped = post_summary["stubs_dropped"],
            duplicates_dropped = post_summary["duplicates_dropped"],
        )

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

        await store.finalize(extra = {
            "framework_name": entry["name"],
            "tier_kind":      base_result["tier_kind"],
            "tier_url":       base_result["tier_url"],
            "run_id":         run_id,
            # Tier 2 llms.txt blockquote summary ("" when absent — Tier 1 and
            # fallback tiers never set it; readers must .get() with fallback).
            "index_summary":  getattr(store, "index_summary", "") or "",
        })

        await progress.finish(status = "done")

        return {
            **base_result,
            "status": "done",
            "pages_written": len(store.manifest),
            "post": post_summary,
            "manifest": [asdict(e) for e in store.manifest],
        }

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
        await progress.finish(status = "cancelled")
        return {
            **base_result, "status": "cancelled", "pages_written": 0,
        }

    except Exception as e:
        logger.exception(f"[dispatch] {slug}: failed")
        # Wipe partial state so the next cached-check sees nothing.
        await _cleanup_framework(minio, slug)
        await progress.finish(status = "failed", error = f"{type(e).__name__}: {e}")
        return {
            **base_result, "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        }

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
