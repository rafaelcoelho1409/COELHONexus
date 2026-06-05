"""plan_write I/O shell — persist the final plan blob (versioned + latest
pointer) + the plan_write_run orchestration."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import numpy as np

from ...ingestion.storage import get_storage
from ..order_chapters import load_chapter_order
from ..progress import emit_progress
from ..state import PlannerState

from .domain import (
    build_cluster_to_keys,
    compute_manifest_hash,
    load_outline,
    sanitize_chapters,
)
from .keys import latest_blob_key, versioned_blob_key
from .versions import PROMPT_VERSION, SCHEMA_VERSION


logger = logging.getLogger(__name__)


async def persist_plan(
    minio,
    *,
    versioned_key: str,
    latest_key: str,
    plan: dict,
) -> None:
    """Hash-keyed versioned blob + mutable latest pointer (no S3 symlink)."""
    plan_bytes = json.dumps(plan, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, plan_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, plan_bytes, content_type = "application/json",
    )


async def plan_write_run(state: PlannerState) -> dict:
    """Final-plan persist: load outline → optional pedagogical reorder →
    sanitize (no LLM) → inline provenance refs (Atlas/SLSA) → versioned + latest."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    chapter_plan_ref = state.get("chapter_plan_ref") or ""
    embeddings_ref = state.get("embeddings_ref") or ""

    if not slug or not chapter_plan_ref:
        return {"plan_path": "", "status": "done"}

    t0 = time.monotonic()

    manifest_hash = compute_manifest_hash(chapter_plan_ref, SCHEMA_VERSION)
    versioned_key = versioned_blob_key(slug, manifest_hash)
    latest_key = latest_blob_key(slug)
    minio = get_storage()

    # Unconditional `start` so the UI shows running even on cache hit.
    await emit_progress(
        thread_id, "plan_write", "start",
        manifest_hash = manifest_hash,
    )

    if (await minio.exists(versioned_key)
            and await minio.exists(latest_key)):
        try:
            latest_text = await minio.read_text(latest_key)
            latest = json.loads(latest_text) or {}
            if latest.get("manifest_hash") == manifest_hash:
                chapters = latest.get("chapters") or []
                latest_stats = latest.get("stats") or {}
                n_sources = sum(
                    len(c.get("sources") or []) for c in chapters
                )
                n_unassigned_cached = len(latest.get("unassigned") or [])
                elapsed = int((time.monotonic() - t0) * 1000)
                stats = {
                    "n_chapters":     len(chapters),
                    "n_sources":      n_sources,
                    "n_unassigned":   latest_stats.get(
                        "n_unassigned", n_unassigned_cached,
                    ),
                    "n_dropped":      latest_stats.get("n_dropped", 0),
                    "wall_ms":        elapsed,
                    "store_path":     latest_key,
                    "versioned_path": versioned_key,
                    "manifest_hash":  manifest_hash,
                    "cache_hit":      True,
                    "plan":           latest,
                }
                await emit_progress(
                    thread_id, "plan_write", "done",
                    n_chapters = len(chapters),
                    n_sources = n_sources,
                    n_unassigned = stats["n_unassigned"],
                    n_dropped = stats["n_dropped"],
                    wall_ms = elapsed, cache_hit = True,
                )
                logger.info(
                    f"[plan_write] {slug}: CACHE HIT — {len(chapters)} "
                    f"chapters, {n_sources} sources, {elapsed} ms"
                )
                return {
                    "plan_path": latest_key,
                    "plan_write_stats": stats,
                    "status": "done",
                }
        except Exception as e:
            logger.warning(
                f"[plan_write] {slug}: cached latest unreadable "
                f"({type(e).__name__}: {e}); regenerating"
            )

    outline_text = await minio.read_text(chapter_plan_ref)
    outline = load_outline(outline_text)

    cluster_keys: list[str] = []
    refined_assignments_list: list[int] = []
    for synth_cid, ch in enumerate((outline or {}).get("chapters") or []):
        mdk = (ch or {}).get("member_doc_keys") or []
        if isinstance(ch, dict):
            ch["member_cluster_ids"] = [synth_cid]
        for k in mdk:
            cluster_keys.append(k)
            refined_assignments_list.append(synth_cid)
    refined_assignments = np.array(
        refined_assignments_list, dtype = np.int64,
    )

    await emit_progress(
        thread_id, "plan_write", "loaded",
        n_chapters_in = len((outline or {}).get("chapters") or []),
        n_clusters = len({
            int(c) for c in refined_assignments if int(c) >= 0
        }),
        n_docs = len(cluster_keys),
    )

    order_ref = state.get("chapter_order_ref") or ""
    raw_chapters = (outline or {}).get("chapters") or []
    reorder_applied = False
    if order_ref and raw_chapters:
        try:
            order_text = await minio.read_text(order_ref)
            order = load_chapter_order(order_text)
            if order is not None and len(order) == len(raw_chapters):
                reordered = [raw_chapters[i] for i in order]
                for new_pos, ch in enumerate(reordered):
                    if isinstance(ch, dict):
                        ch["order"] = new_pos + 1
                raw_chapters = reordered
                reorder_applied = True
                await emit_progress(
                    thread_id, "plan_write", "reordered",
                    order = order,
                )
                logger.info(
                    f"[plan_write] {slug}: applied pedagogical order "
                    f"from {order_ref!r}: {order}"
                )
            else:
                logger.warning(
                    f"[plan_write] {slug}: chapter_order_ref "
                    f"{order_ref!r} length mismatch (got "
                    f"{len(order) if order else 'None'}, expected "
                    f"{len(raw_chapters)}); identity order kept"
                )
        except Exception as e:
            logger.warning(
                f"[plan_write] {slug}: chapter_order_ref {order_ref!r} "
                f"unreadable ({type(e).__name__}: {e}); identity order "
                f"kept"
            )

    cluster_to_keys = build_cluster_to_keys(
        refined_assignments, cluster_keys,
    )
    chapters, n_dropped = sanitize_chapters(raw_chapters, cluster_to_keys)
    n_sources_total = sum(len(c["sources"]) for c in chapters)

    unassigned_keys = sorted(set(cluster_keys) - {
        k for c in chapters for k in c["sources"]
    })

    await emit_progress(
        thread_id, "plan_write", "sanitized",
        n_chapters = len(chapters),
        n_dropped = n_dropped,
        n_sources = n_sources_total,
        n_unassigned = len(unassigned_keys),
    )

    plan = {
        "schema_version": SCHEMA_VERSION,
        "framework_slug": slug,
        "manifest_hash":  manifest_hash,
        "generated_at":   datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
        "chapters":       chapters,
        "unassigned":     unassigned_keys,
        "provenance": {
            "embeddings_ref":    embeddings_ref,
            "chapter_plan_ref":  chapter_plan_ref,
            "prompt_versions":   {"plan_write": PROMPT_VERSION},
            "corpus_doc_count":  len(cluster_keys),
            "chapter_count":     len({
                int(c) for c in refined_assignments if int(c) >= 0
            }),
        },
        "stats": {
            "n_chapters":   len(chapters),
            "n_sources":    n_sources_total,
            "n_unassigned": len(unassigned_keys),
            "n_dropped":    n_dropped,
        },
    }

    await persist_plan(
        minio,
        versioned_key = versioned_key,
        latest_key = latest_key,
        plan = plan,
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_chapters":      len(chapters),
        "n_sources":       n_sources_total,
        "n_unassigned":    len(unassigned_keys),
        "n_dropped":       n_dropped,
        "wall_ms":         elapsed,
        "store_path":      latest_key,
        "versioned_path":  versioned_key,
        "manifest_hash":   manifest_hash,
        "cache_hit":       False,
        "reorder_applied": reorder_applied,
        "plan":            plan,
    }
    await emit_progress(
        thread_id, "plan_write", "done",
        n_chapters = len(chapters), n_sources = n_sources_total,
        n_unassigned = len(unassigned_keys),
        n_dropped = n_dropped, wall_ms = elapsed,
    )
    logger.info(
        f"[plan_write] {slug}: {len(chapters)} chapters, "
        f"{n_sources_total} sources, {n_dropped} dropped, "
        f"{len(unassigned_keys)} unassigned; wrote {latest_key} + "
        f"{versioned_key} in {elapsed} ms"
    )
    return {
        "plan_path": latest_key,
        "plan_write_stats": stats,
        "status": "done",
    }
