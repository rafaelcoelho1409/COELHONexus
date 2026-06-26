"""Async orchestration: tier dispatch, fallthrough, post-process, finalize.

Cancel is cooperative — tier modules call `progress.raise_if_cancelled()`;
the watcher coroutine also pre-empts blocking awaits. Lock TTL (35 min)
outlives Celery soft_time_limit (30 min) so a crashed task self-releases.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

import redis.asyncio as redis_aio

from infra.otel import get_tracer

from ...resolver import index_by_slug
from .. import post
from ..observability import record_ingestion_run
from ..progress import (
    IngestCancelled,
    Progress,
    is_cancelled,
    redis_url,
    release_lock,
)
from ..storage import framework_prefix, get_storage, Store
from ..tiers import (
    EmptyLinksDetected,
    ManifestDetected,
    tier1,
    tier2,
    tier3,
    tier4,
    tier5,
)
from .domain import pick_best
from .params import (
    CANCEL_POLL_S,
    CLEANUP_SETTLE_S,
    REDIS_CONNECT_TIMEOUT_S,
    REDIS_OP_TIMEOUT_S,
)


logger = logging.getLogger(__name__)


_TIER_MODULES = {
    "llms_full": tier1,
    "llms_txt":  tier2,
    "sitemap":   tier3,
    "docs":      tier4,
    "github":    tier5,
}


async def _cancel_watcher(
    redis_client: "redis_aio.Redis",
    run_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = CANCEL_POLL_S,
) -> None:
    """Pre-empts blocking awaits (Crawl4AI `arun_many` can block 30-60s).
    Bypasses Progress throttle by calling `is_cancelled()` directly — sleep
    is the rate limit."""
    try:
        while not main_task.done():
            try:
                if await is_cancelled(redis_client, run_id):
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
        n = await minio.delete_prefix(framework_prefix(framework_slug))
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
    record_ingestion_run(
        framework = slug,
        tier_kind = str(result.get("tier_kind") or "unknown"),
        outcome = str(result.get("status") or "unknown"),
        duration_s = max(asyncio.get_running_loop().time() - t0, 0.0),
        output_files = int(post_summary.get("output_files", 0) or 0),
        output_bytes = int(post_summary.get("output_bytes", 0) or 0),
    )
    return result


async def _run_inner(run_id: str, slug: str) -> dict:
    """Run ingestion for `slug` under `run_id`. Framework lock is held by
    `run_id` on entry (acquired in POST /runs); released in `finally`."""
    catalog = index_by_slug()
    entry = catalog.get(slug)
    if entry is None:
        return {
            "run_id": run_id, "slug": slug, "status": "failed",
            "error": f"unknown framework slug: {slug!r}",
        }

    best = pick_best(entry)
    if best is None:
        return {
            "run_id": run_id, "slug": slug,
            "framework_name": entry["name"],
            "status": "failed",
            "error": "no source URLs in catalog entry",
        }

    progress = Progress(run_id)
    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = REDIS_OP_TIMEOUT_S,
    )
    minio = get_storage()
    store = Store(run_id, slug, r, minio)

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
            if mod in (tier3, tier4):
                kwargs["framework_name"] = entry["name"]
                kwargs["path_filter"] = entry.get("path_filter")
            await mod.run(**kwargs)
        except ManifestDetected:
            if entry.get("llms_txt"):
                logger.info(
                    f"[dispatch] {slug}: Tier 1 manifest detected, "
                    f"falling through to Tier 2"
                )
                base_result["tier_kind"] = "llms_txt"
                base_result["tier_url"] = entry["llms_txt"]
                await progress.close()
                progress = Progress(run_id)
                await tier2.run(
                    url = entry["llms_txt"], framework_slug = slug,
                    progress = progress, store = store,
                )
            else:
                raise RuntimeError(
                    f"Tier 1 manifest at {url} but no llms_txt URL "
                    f"available to fall back to"
                )
        except EmptyLinksDetected:
            # llms.txt long-form prose with no per-page links — fall through.
            fallback_chain = [
                ("sitemap", entry.get("sitemap"), tier3),
                ("docs",    entry.get("docs"),    tier4),
                ("github",  entry.get("github"),  tier5),
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
            progress = Progress(run_id)
            fb_kwargs = {
                "url": fb_url, "framework_slug": slug,
                "progress": progress, "store": store,
            }
            if fb_mod in (tier3, tier4):
                fb_kwargs["framework_name"] = entry["name"]
                fb_kwargs["path_filter"] = entry.get("path_filter")
            await fb_mod.run(**fb_kwargs)

        await progress.raise_if_cancelled()

        # Tier returned 'done'; UI must wait for post + finalize.
        await progress.start(tier = "post", total = 0)

        post_summary = await post.apply_to_store(store)
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

        # Canonical manifest — written only on success.
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

    except (IngestCancelled, asyncio.CancelledError):
        # Watcher path → CancelledError; cooperative path → IngestCancelled.
        logger.info(f"[dispatch] {slug}: cancelled by user (run_id={run_id})")
        # CRITICAL: stop the watcher BEFORE cleanup. Otherwise its poll loop
        # (cancel-flag still set in Redis) fires a SECOND main_task.cancel()
        # mid-cleanup → CancelledError raised inside delete_prefix → cleanup
        # aborts with leftover MinIO objects (observed: 428 stragglers).
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass
        # Two-pass cleanup: tier 2/3/4a stream in parallel and may finish
        # MinIO writes AFTER cancel but BEFORE gather unwinds. Settle gap
        # lets in-flight writes finish; second sweep catches stragglers.
        n1 = await _cleanup_framework(minio, slug)
        await asyncio.sleep(CLEANUP_SETTLE_S)
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
            await release_lock(r, slug, run_id)
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
