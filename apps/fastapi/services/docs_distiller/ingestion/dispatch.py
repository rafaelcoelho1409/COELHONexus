"""Ingestion entry point.

Given a `run_id` + framework `slug`, look up the resolver's best-tier
source, dispatch to the matching tier module, post-process, and finalize
the canonical per-framework manifest in MinIO.

Cooperative cancel: tier modules call `progress.raise_if_cancelled()`
between fetches; the resulting `IngestCancelled` triggers a cleanup pass
that wipes the partial framework prefix from MinIO so the next run starts
from a clean slate.

The single-flight lock is acquired by the HTTP layer (`POST /runs`) before
Celery is even dispatched; this module just releases it in the `finally`
block. TTL on the lock (35 min) outlives the Celery task's soft_time_limit
(30 min), so a crashed task still releases the lock automatically.
"""
import logging
from dataclasses import asdict

import redis.asyncio as redis_aio

from routers.v1.docs_distiller.resolver import _index_by_slug

from . import (
    post,
    tier1_llms_full,
    tier2_llms_txt,
    tier3_sitemap,
    tier4_http,
    tier5_github,
)
from .progress import (
    IngestCancelled,
    Progress,
    _redis_url,
    release_lock,
)
from .storage_minio import framework_prefix, get_storage
from .store import Store


logger = logging.getLogger(__name__)


_TIER_MODULES = {
    "llms_full": tier1_llms_full,
    "llms_txt": tier2_llms_txt,
    "sitemap": tier3_sitemap,
    "docs": tier4_http,
    "github": tier5_github,
}


def _pick_best(entry: dict) -> dict | None:
    for kind in ("llms_full", "llms_txt", "sitemap", "docs", "github"):
        if entry.get(kind):
            return {"kind": kind, "url": entry[kind]}
    return None


async def _cleanup_framework(minio, framework_slug: str) -> int:
    """Wipe everything under `ingestion/{slug}/` in MinIO. Used by the
    cancel + hard-fail paths so partial corpora don't get reused."""
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
    """Run ingestion for `slug` under `run_id`. The framework lock is
    assumed already held by `run_id` (acquired in POST /runs); this
    function releases it in the `finally` block.

    Result shape:
        {
            "run_id": "...",
            "slug": "...",
            "framework_name": "...",
            "tier_kind": "llms_full",
            "tier_url": "https://...",
            "pages_written": 137,
            "post": {...},
            "manifest": [...],
            "status": "done" | "failed" | "cancelled",
            "error": "..."           # only on failure
        }
    """
    catalog = _index_by_slug()
    entry = catalog.get(slug)
    if entry is None:
        return {
            "run_id": run_id, "slug": slug, "status": "failed",
            "error": f"unknown framework slug: {slug!r}",
        }

    best = _pick_best(entry)
    if best is None:
        return {
            "run_id": run_id, "slug": slug,
            "framework_name": entry["name"],
            "status": "failed",
            "error": "no source URLs in catalog entry",
        }

    progress = Progress(run_id)
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=10.0,
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

    try:
        await progress.raise_if_cancelled()

        # Tier dispatch (Tier 1 may detect a manifest → fall to Tier 2)
        kind = best["kind"]
        url = best["url"]
        try:
            mod = _TIER_MODULES[kind]
            kwargs: dict = {
                "url": url, "framework_slug": slug,
                "progress": progress, "store": store,
            }
            if mod in (tier3_sitemap, tier4_http):
                kwargs["framework_name"] = entry["name"]
            await mod.run(**kwargs)
        except tier1_llms_full.ManifestDetected:
            if entry.get("llms_txt"):
                logger.info(
                    f"[dispatch] {slug}: Tier 1 manifest detected, "
                    f"falling through to Tier 2"
                )
                base_result["tier_kind"] = "llms_txt"
                base_result["tier_url"] = entry["llms_txt"]
                await progress.close()
                progress = Progress(run_id)
                await tier2_llms_txt.run(
                    url=entry["llms_txt"], framework_slug=slug,
                    progress=progress, store=store,
                )
            else:
                raise RuntimeError(
                    f"Tier 1 manifest at {url} but no llms_txt URL "
                    f"available to fall back to"
                )

        await progress.raise_if_cancelled()

        # Tier modules flag status='done' before returning; reset to a
        # post-processing phase so the UI doesn't see "done" prematurely
        # and try to load the (not-yet-finalized) canonical manifest.
        await progress.start(tier="post", total=0)

        # Post-process — split monolith / dedup pages
        post_summary = await post.apply_to_store(store)
        await progress.record_post(
            tier=base_result["tier_kind"],
            input_files=post_summary["input_files"],
            input_bytes=post_summary["input_bytes"],
            output_files=post_summary["output_files"],
            output_bytes=post_summary["output_bytes"],
            was_split=post_summary["was_split"],
            stubs_dropped=post_summary["stubs_dropped"],
            duplicates_dropped=post_summary["duplicates_dropped"],
        )

        await progress.raise_if_cancelled()

        # Same trick — finalize is fast but worth marking distinctly.
        await progress.start(tier="finalize", total=0)

        # Finalize the canonical per-framework manifest in MinIO. This
        # is what the library view + cached-check read; written once
        # per successful ingest so partial / cancelled runs never expose
        # a half-baked manifest.
        await store.finalize(extra={
            "framework_name": entry["name"],
            "tier_kind": base_result["tier_kind"],
            "tier_url": base_result["tier_url"],
            "run_id": run_id,
        })

        # Only NOW the pipeline is truly complete.
        await progress.finish(status="done")

        return {
            **base_result,
            "status": "done",
            "pages_written": len(store.manifest),
            "post": post_summary,
            "manifest": [asdict(e) for e in store.manifest],
        }

    except IngestCancelled:
        logger.info(f"[dispatch] {slug}: cancelled by user (run_id={run_id})")
        await _cleanup_framework(minio, slug)
        await progress.finish(status="cancelled")
        return {
            **base_result, "status": "cancelled", "pages_written": 0,
        }

    except Exception as e:
        logger.exception(f"[dispatch] {slug}: failed")
        # Wipe partial state so the next attempt starts clean. This is
        # safer than leaving a half-written prefix that might confuse the
        # cached-check on the next POST /runs.
        await _cleanup_framework(minio, slug)
        await progress.finish(status="failed")
        return {
            **base_result, "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        }

    finally:
        # Always release the lock so a subsequent click can proceed.
        try:
            await release_lock(r, slug, run_id)
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
