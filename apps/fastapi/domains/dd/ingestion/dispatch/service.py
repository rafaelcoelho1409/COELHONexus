"""Cancel is cooperative (progress.raise_if_cancelled + watcher pre-empts blocking awaits). Lock TTL (35 min) outlasts Celery soft_time_limit (30 min) so crashed tasks self-release."""
from __future__ import annotations
import domains
from . import domain, params
# Module-level dict below needs the tier submodules RESOLVED at
# dispatch/service.py's own import time — the global `domains.dd...`
# chase can't be used here (docs/CODE-CONVENTIONS.md §8 Exception 2:
# `domains.dd` isn't set until `dd/__init__.py` fully finishes, and
# `dispatch` imports before `tiers` in ingestion/__init__.py's eager
# chain). A sibling-rooted `from .. import tiers` is always safe at
# module level since that import already blocked until `tiers` was
# fully ready.
from .. import tiers

import asyncio
import logging
from dataclasses import asdict

import redis.asyncio as redis_aio

from infra.otel import get_tracer



logger = logging.getLogger(__name__)


_TIER_MODULES = {
    "llms_full": tiers.tier1,
    "llms_txt":  tiers.tier2,
    "sitemap":   tiers.tier3,
    "docs":      tiers.tier4,
    "github":    tiers.tier5,
}


async def _cancel_watcher(
    redis_client: "redis_aio.Redis",
    run_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = params.CANCEL_POLL_S,
) -> None:
    """Bypasses Progress throttle (calls is_cancelled() directly; sleep is the rate limit). Crawl4AI arun_many can block 30-60 s."""
    try:
        while not main_task.done():
            try:
                if await domains.dd.ingestion.progress.service.is_cancelled(redis_client, run_id):
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


async def run(run_id: str, slug: str) -> dict:
    """Span + metrics wrapper around the ingestion dispatcher."""
    t0 = asyncio.get_running_loop().time()
    with get_tracer().start_as_current_span(
        "dd.ingestion.run",
        attributes = {
            "dd.domain":                "ingestion",
            "dd.run.kind":              "ingestion",
            "ingestion.run_id":         run_id,
            "ingestion.framework_slug": slug,
        },
    ):
        result = await _run_inner(run_id, slug)
    post_summary = result.get("post") or {}
    domains.dd.ingestion.observability.record_ingestion_run(
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

    progress = domains.dd.ingestion.progress.service.Progress(run_id)
    r = redis_aio.from_url(
        domains.dd.ingestion.progress.keys.redis_url(),
        socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = params.REDIS_OP_TIMEOUT_S,
    )
    minio = domains.dd.ingestion.storage.service.get_storage()
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

        kind = best["kind"]
        url = best["url"]
        try:
            mod = _TIER_MODULES[kind]
            kwargs: dict = {
                "url": url, "framework_slug": slug,
                "progress": progress, "store": store,
            }
            if mod in (domains.dd.ingestion.tiers.tier3, domains.dd.ingestion.tiers.tier4):
                kwargs["framework_name"] = entry["name"]
                kwargs["path_filter"] = entry.get("path_filter")
            await mod.service.run(**kwargs)
        except domains.dd.ingestion.tiers.errors.ManifestDetected:
            if entry.get("llms_txt"):
                logger.info(
                    f"[dispatch] {slug}: Tier 1 manifest detected, "
                    f"falling through to Tier 2"
                )
                base_result["tier_kind"] = "llms_txt"
                base_result["tier_url"] = entry["llms_txt"]
                await progress.close()
                progress = domains.dd.ingestion.progress.service.Progress(run_id)
                await domains.dd.ingestion.tiers.tier2.service.run(
                    url = entry["llms_txt"], framework_slug = slug,
                    progress = progress, store = store,
                )
            else:
                raise RuntimeError(
                    f"Tier 1 manifest at {url} but no llms_txt URL "
                    f"available to fall back to"
                )
        except domains.dd.ingestion.tiers.errors.EmptyLinksDetected:
            # llms.txt long-form prose with no per-page links — fall through.
            fallback_chain = [
                ("sitemap", entry.get("sitemap"), domains.dd.ingestion.tiers.tier3),
                ("docs",    entry.get("docs"),    domains.dd.ingestion.tiers.tier4),
                ("github",  entry.get("github"),  domains.dd.ingestion.tiers.tier5),
            ]
            picked = next(((k, u, m) for k, u, m in fallback_chain if u), None)
            if picked is None:
                raise RuntimeError(
                    f"Tier 2 llms.txt at {url} yielded zero links and no "
                    f"fallback tier (sitemap/docs/github) is configured"
                )
            fb_kind, fb_url, fb_mod = picked
            logger.info(
                f"[dispatch] {slug}: Tier 2 yielded zero links, "
                f"falling through to Tier {fb_kind} ({fb_url})"
            )
            base_result["tier_kind"] = fb_kind
            base_result["tier_url"] = fb_url
            await progress.close()
            progress = domains.dd.ingestion.progress.service.Progress(run_id)
            fb_kwargs = {
                "url": fb_url, "framework_slug": slug,
                "progress": progress, "store": store,
            }
            if fb_mod in (domains.dd.ingestion.tiers.tier3, domains.dd.ingestion.tiers.tier4):
                fb_kwargs["framework_name"] = entry["name"]
                fb_kwargs["path_filter"] = entry.get("path_filter")
            await fb_mod.service.run(**fb_kwargs)

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

        await progress.start(tier = "finalize", total = 0)

        await store.finalize(extra = {
            "framework_name": entry["name"],
            "tier_kind":      base_result["tier_kind"],
            "tier_url":       base_result["tier_url"],
            "run_id":         run_id,
        })

        await progress.finish(status = "done")

        return {
            **base_result,
            "status": "done",
            "pages_written": len(store.manifest),
            "post": post_summary,
            "manifest": [asdict(e) for e in store.manifest],
        }

    except (domains.dd.ingestion.progress.errors.IngestCancelled, asyncio.CancelledError):
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
        await progress.finish(status = "failed")
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
            await domains.dd.ingestion.progress.service.release_lock(r, slug, run_id)
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
