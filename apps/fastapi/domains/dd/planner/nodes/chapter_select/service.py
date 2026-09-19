"""chapter_select I/O shell — persist the 4-blob output (select-specific +
reduce-compatible plan, each versioned + latest) + the chapter_select_run
orchestration.

SOTA Sept 2026: pure-algorithm node (no LLM rotator) — fastest via parallel
I/O (gather proposals/assignments/seeds, concurrent 4-blob writes) and
vectorized greedy. No coelho-llm-rotator call here, so old built-in vs
pooled is no-op; old rotator never touched this node.
"""
from __future__ import annotations
import domains
from . import domain, keys, versions

import asyncio
import json
import logging
import time


logger = logging.getLogger(__name__)


async def persist_select_outputs(
    minio,
    *,
    slug: str,
    manifest: str,
    select_payload: dict,
    plan_payload: dict,
) -> tuple[str, str]:
    """Write select + plan blobs (versioned + latest) concurrently.
    Returns (plan_latest_key, plan_versioned_key). SOTA: 4 writes via
    gather on shared S3 client (vs 4 sequential) — ~4× for MinIO 4-blob."""
    select_blob = json.dumps(
        select_payload, indent = 2, ensure_ascii = False,
    )
    plan_blob = json.dumps(plan_payload, indent = 2, ensure_ascii = False)

    vkey_select = keys.select_versioned_key(slug, manifest)
    lkey_select = keys.select_latest_key(slug)
    plan_vkey = keys.chapter_plan_versioned_key(slug, manifest)
    plan_lkey = keys.chapter_plan_latest_key(slug)

    # Concurrent writes — shared client per chunk in storage.service handles
    # pooling; gather here removes sequential 4× RTT.
    await asyncio.gather(
        minio.write(vkey_select, select_blob, content_type = "application/json"),
        minio.write(lkey_select, select_blob, content_type = "application/json"),
        minio.write(plan_vkey, plan_blob, content_type = "application/json"),
        minio.write(plan_lkey, plan_blob, content_type = "application/json"),
    )

    return plan_lkey, plan_vkey


async def chapter_select_run(state: domains.dd.planner.state.PlannerState) -> dict:
    """Pure-algorithm node: load → greedy-coverage (with pin override) →
    prune (<MIN_DOCS unless pinned) → re-assign survivors → persist."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    proposals_ref = state.get("chapter_proposals_ref")
    assignments_ref = state.get("chapter_doc_assignments_ref")

    if not slug or not proposals_ref or not assignments_ref:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "missing_inputs"},
        }

    t0 = time.monotonic()
    minio = domains.dd.ingestion.storage.service.get_storage()

    manifest = domain.manifest_hash(
        slug = slug,
        proposals_ref = proposals_ref,
        assignments_ref = assignments_ref,
    )

    # Parallel load: proposals + assignments + seeds in one gather (3× RTT → 1×)
    # Pure I/O, no LLM — old built-in never touched rotator here, so SOTA is
    # just I/O concurrency, not pooling.
    async def _load_seeds() -> dict:
        try:
            txt = await minio.read_text(proposals_ref)
            return (json.loads(txt) or {}).get("seeds") or {}
        except Exception:
            return {}

    proposals_obj, assignments, seeds = await asyncio.gather(
        domains.dd.planner.nodes.chapter_propose.service.load_proposals(minio, slug),
        domains.dd.planner.nodes.chapter_assign.service.load_assignments(minio, slug),
        _load_seeds(),
    )
    if proposals_obj is None or not proposals_obj.proposals:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "no_proposals"},
        }
    proposals = [p.model_dump() for p in proposals_obj.proposals]
    if not assignments:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "no_assignments"},
        }

    pinned = domain.detect_pinned_indices(proposals, seeds)

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_select", "start",
        n_proposals = len(proposals),
        n_docs = len(assignments),
        n_pinned = len(pinned),
    )

    selected, doc_to_chapter = domain.greedy_select(
        proposals = proposals, assignments = assignments,
        pinned_indices = pinned,
    )

    final = domain.prune_and_finalize_selection(
        selected = selected,
        doc_to_chapter = doc_to_chapter,
        assignments = assignments,
        pinned = pinned,
        proposals = proposals,
    )
    kept = final["kept"]
    pruned = final["pruned"]
    orphan_protected = final["orphan_protected"]
    doc_to_chapter = final["doc_to_chapter"]
    out_chapters = final["out_chapters"]

    n_assigned_docs = len(doc_to_chapter)
    n_total_docs = len(assignments)

    select_payload = {
        "prompt_version":        versions.PROMPT_VERSION,
        "framework_slug":        slug,
        "manifest_hash":         manifest,
        "selected_indices":      kept,
        "pruned_indices":        pruned,
        "pinned_indices":        sorted(pinned),
        "orphan_protected":      orphan_protected,
        "n_proposals_in":        len(proposals),
        "n_chapters_out":        len(kept),
        "n_orphan_protected":    len(orphan_protected),
        "n_assigned_docs":       n_assigned_docs,
        "n_total_docs":          n_total_docs,
        "coverage_fraction":     (
            n_assigned_docs / n_total_docs if n_total_docs else 0.0
        ),
        "chapters":              out_chapters,
    }
    plan_payload = {
        "prompt_version":  versions.PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "outline": {
            "chapters": [
                {
                    "title":              c["title"],
                    "description":        c["description"],
                    "member_cluster_ids": [],   # n/a — LLM-first path
                    "member_doc_keys":    c["member_doc_keys"],
                    "order":              c["order"],
                }
                for c in out_chapters
            ],
        },
        "n_clusters_in":   len(proposals),
        "n_chapters_out":  len(kept),
        "n_repairs":       0,
        "forced_repair":   False,
        "source":          "llm_first_chapter_select_v1",
    }

    plan_lkey, _plan_vkey = await persist_select_outputs(
        minio,
        slug = slug,
        manifest = manifest,
        select_payload = select_payload,
        plan_payload = plan_payload,
    )

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_proposals_in":     len(proposals),
        "n_chapters_out":     len(kept),
        "n_pruned":           len(pruned),
        "n_orphan_protected": len(orphan_protected),
        "n_assigned_docs":    n_assigned_docs,
        "n_total_docs":       n_total_docs,
        "coverage_fraction":  (
            n_assigned_docs / n_total_docs if n_total_docs else 0.0
        ),
        "wall_ms":            wall_ms,
        "manifest_hash":      manifest,
        "chapter_titles":     [c["title"] for c in out_chapters],
        "chapter_sizes":      [c["n_member_docs"] for c in out_chapters],
    }
    if orphan_protected:
        logger.info(
            f"[chapter_select] {slug}: orphan-protected "
            f"{len(orphan_protected)} small chapter(s) whose members had "
            f"no alternative above-threshold chapter: {orphan_protected}"
        )
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_select", "done",
        n_chapters = len(kept),
        n_pruned = len(pruned),
        n_orphan_protected = len(orphan_protected),
        coverage = stats["coverage_fraction"],
        wall_ms = wall_ms,
        titles = stats["chapter_titles"],
    )
    return {"chapter_plan_ref": plan_lkey, "select_stats": stats}
