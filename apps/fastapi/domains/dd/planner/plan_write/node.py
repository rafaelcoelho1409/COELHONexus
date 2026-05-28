"""Substep 8 — plan_write: persist the FINAL chapter plan to MinIO.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA report
(SurveyGen-I arXiv 2508.14317 + SurveyForge arXiv 2503.04629 +
LLMxMapReduce-V2 arXiv 2504.05732 + TnT-LLM arXiv 2403.12173 +
Atlas/SLSA v1.1 provenance idioms). Pipeline:

  1. Load reduce outline + refine assignments + cluster keys + labels.
  2. Hydrate each chapter's `sources` from refined cluster_id → MinIO
     doc-key map (flat array of keys per SurveyForge / LLMxMapReduce —
     downstream chapter synthesizer does its own read_many()).
  3. Light sanitization (~no LLM): smart title-case, description trim
     and clamp, drop chapters with empty sources, re-number `order`
     1..N contiguous, generate stable `id = ch-{order}-{slug}`.
  4. Embed upstream provenance refs inline (5 *_ref pointers + prompt
     versions + corpus_doc_count) per Atlas/SLSA "consumer-facing
     artifact carries digests of its inputs" pattern.
  5. Write the hash-keyed versioned blob at
     `planner/{slug}/plan/{hash}.json`, then PUT a mutable latest
     pointer at `planner/{slug}/plan-latest.json` (MinIO/S3 has no
     symlink — small mutable object is the idiomatic move).

State writes:
  plan_path — MinIO key of the LATEST pointer (the consumer-facing key)

Notes:
- NO Self-Refine pass on the outline (Madaan 2023 gains are on
  creative/code, not on already-validated structural outputs;
  SurveyForge refines during writing, not post-hoc). Skipped per
  research recommendation — spend the rotator budget on chapter
  synthesis instead.
- The reduce node already produces a content-addressed hash-keyed
  blob at `planner/{slug}/chapters/{hash}.json`; this node produces
  a CONSUMER-facing artifact with file lists hydrated.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ...ingestion.storage import get_storage

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from ..cluster import load_clusters
from ..label import load_labels
from ..order_chapters import load_chapter_order
from ..reduce import load_outline
from ..refine import load_refine

from .constants import (
    _PROMPT_VERSION,
    _SCHEMA_VERSION,
)
from .service import (
    _build_cluster_to_keys,
    _compute_manifest_hash,
    _latest_blob_key,
    _sanitize_chapters,
    _versioned_blob_key,
)


logger = logging.getLogger(__name__)


@traced("plan_write")
async def plan_write(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    refine_ref = state.get("refine_assignments_ref") or ""
    labels_ref = state.get("cluster_labels_ref") or ""
    reduce_ref = state.get("chapter_plan_ref") or ""
    embeddings_ref = state.get("embeddings_ref") or ""

    # 2026-05-27 — tolerate missing cluster/refine refs on the LLM-first
    # path (KD_PLANNER_LLM_FIRST=true). In that mode, chapter_select wrote
    # `member_doc_keys` directly to the chapter_plan_ref outline; we hydrate
    # sources from those instead of looking up cluster→keys.
    llm_first_mode = (not cluster_ref) or (not refine_ref)
    if not slug or not reduce_ref:
        return {
            "plan_path": "",
            "status": "done",
        }

    t0 = time.monotonic()

    manifest_hash = _compute_manifest_hash(
        cluster_ref, refine_ref, labels_ref, reduce_ref, _SCHEMA_VERSION,
    )
    versioned_key = _versioned_blob_key(slug, manifest_hash)
    latest_key = _latest_blob_key(slug)
    minio = get_storage()

    # Emit `start` unconditionally so the UI shows a live "running"
    # status line even on cache hit (other nodes follow the same
    # convention — see label.py / reduce.py SSE flow).
    await emit_progress(
        thread_id, "plan_write", "start",
        manifest_hash=manifest_hash,
    )

    # ── Cache fast-path ────────────────────────────────────────────────
    # BOTH the hash-keyed blob AND the latest pointer must exist; if the
    # latest pointer is missing or points to a different hash, we
    # rewrite it.
    if await minio.exists(versioned_key) and await minio.exists(latest_key):
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
                    n_chapters=len(chapters),
                    n_sources=n_sources,
                    n_unassigned=stats["n_unassigned"],
                    n_dropped=stats["n_dropped"],
                    wall_ms=elapsed, cache_hit=True,
                )
                logger.info(
                    f"[plan_write] {slug}: CACHE HIT — {len(chapters)} "
                    f"chapters, {n_sources} sources, {elapsed} ms"
                )
                return {"plan_path": latest_key, "plan_write_stats": stats,
                        "status": "done"}
        except Exception as e:
            logger.warning(
                f"[plan_write] {slug}: cached latest unreadable "
                f"({type(e).__name__}: {e}); regenerating"
            )

    # ── Load upstream artifacts ────────────────────────────────────────
    # LLM-first path (chapter_select): no cluster/refine/labels artifacts
    # — chapters carry `member_doc_keys` directly in the outline.
    reduce_text = await minio.read_text(reduce_ref)
    outline = load_outline(reduce_text)

    if llm_first_mode:
        # Skip cluster/refine/labels loads. Build a synthetic
        # cluster_to_keys from outline.chapters' member_doc_keys + assign
        # each chapter a synthetic cluster_id = its order.
        cluster_keys = []
        refined_assignments_list: list[int] = []
        # Synthetic: each chapter index = its synthetic cluster_id.
        for synth_cid, ch in enumerate((outline or {}).get("chapters") or []):
            mdk = (ch or {}).get("member_doc_keys") or []
            # ensure the chapter carries member_cluster_ids consistent with
            # the synthetic id so _sanitize_chapters' cluster lookup works.
            if isinstance(ch, dict):
                ch["member_cluster_ids"] = [synth_cid]
            for k in mdk:
                cluster_keys.append(k)
                refined_assignments_list.append(synth_cid)
        import numpy as _np
        refined_assignments = _np.array(refined_assignments_list, dtype=_np.int64)
        labels: dict[int, str] = {
            synth_cid: ((outline or {}).get("chapters") or [{}])[synth_cid].get("title") or ""
            for synth_cid in range(len((outline or {}).get("chapters") or []))
        }
    else:
        cluster_blob = await minio.read_bytes(cluster_ref)
        cluster_keys, _orig_assigns, _max_probs, _soft = load_clusters(cluster_blob)
        refine_blob = await minio.read_bytes(refine_ref)
        refine_keys, refined_assignments, _, _ = load_refine(refine_blob)
        if cluster_keys != refine_keys:
            raise RuntimeError(
                f"plan_write: key mismatch — cluster has {len(cluster_keys)} "
                f"keys, refine has {len(refine_keys)}; pipeline integrity broken"
            )
        labels_text = await minio.read_text(labels_ref)
        labels = load_labels(labels_text)

    await emit_progress(
        thread_id, "plan_write", "loaded",
        n_chapters_in=len((outline or {}).get("chapters") or []),
        n_clusters=len({int(c) for c in refined_assignments if int(c) >= 0}),
        n_docs=len(cluster_keys),
    )

    # ── Bundle 8 (2026-05-25) — Pedagogical reorder ──────────────────
    # If order_chapters wrote a chapter_order_ref, apply that permutation to
    # the outline's chapters list BEFORE sanitization. Fail-soft: if the
    # ordering blob is missing / malformed / has wrong length, fall back to
    # whatever order reduce emitted (identity).
    order_ref = state.get("chapter_order_ref") or ""
    raw_chapters = (outline or {}).get("chapters") or []
    reorder_applied = False
    if order_ref and raw_chapters:
        try:
            order_text = await minio.read_text(order_ref)
            order = load_chapter_order(order_text)
            if order is not None and len(order) == len(raw_chapters):
                # Apply permutation; rewrite the 1-based `order` field so
                # downstream consumers (UI / sanitization) see the new sequence.
                reordered = [raw_chapters[i] for i in order]
                for new_pos, ch in enumerate(reordered):
                    if isinstance(ch, dict):
                        ch["order"] = new_pos + 1
                raw_chapters = reordered
                reorder_applied = True
                await emit_progress(
                    thread_id, "plan_write", "reordered",
                    order=order,
                )
                logger.info(
                    f"[plan_write] {slug}: applied pedagogical order from "
                    f"{order_ref!r}: {order}"
                )
            else:
                logger.warning(
                    f"[plan_write] {slug}: chapter_order_ref {order_ref!r} "
                    f"length mismatch (got {len(order) if order else 'None'}, "
                    f"expected {len(raw_chapters)}); identity order kept"
                )
        except Exception as e:
            logger.warning(
                f"[plan_write] {slug}: chapter_order_ref {order_ref!r} "
                f"unreadable ({type(e).__name__}: {e}); identity order kept"
            )

    # ── Hydrate + sanitize ─────────────────────────────────────────────
    cluster_to_keys = _build_cluster_to_keys(refined_assignments, cluster_keys)
    chapters, n_dropped = _sanitize_chapters(raw_chapters, cluster_to_keys)
    n_sources_total = sum(len(c["sources"]) for c in chapters)

    # Account for docs that ended up in NO chapter (cluster id reduce
    # never assigned, or noise that was orphaned).
    unassigned_keys = sorted(set(cluster_keys) - {
        k for c in chapters for k in c["sources"]
    })

    await emit_progress(
        thread_id, "plan_write", "sanitized",
        n_chapters=len(chapters), n_dropped=n_dropped,
        n_sources=n_sources_total, n_unassigned=len(unassigned_keys),
    )

    # ── Build the consumer-facing payload ──────────────────────────────
    plan = {
        "schema_version": _SCHEMA_VERSION,
        "framework_slug": slug,
        "manifest_hash":  manifest_hash,
        "generated_at":   datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
        "chapters":       chapters,
        "unassigned":     unassigned_keys,
        "provenance": {
            "embeddings_ref":  embeddings_ref,
            "cluster_ref":     cluster_ref,
            "refine_ref":      refine_ref,
            "labels_ref":      labels_ref,
            "reduce_ref":      reduce_ref,
            "prompt_versions": {"plan_write": _PROMPT_VERSION},
            "corpus_doc_count": len(cluster_keys),
            "cluster_count":   len({
                int(c) for c in refined_assignments if int(c) >= 0
            }),
            "label_count":     sum(1 for lid in labels if int(lid) >= 0),
        },
        "stats": {
            "n_chapters":   len(chapters),
            "n_sources":    n_sources_total,
            "n_unassigned": len(unassigned_keys),
            "n_dropped":    n_dropped,
        },
    }

    # ── Persist: hash-keyed + latest pointer ───────────────────────────
    plan_bytes = json.dumps(plan, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, plan_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, plan_bytes, content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_chapters":     len(chapters),
        "n_sources":      n_sources_total,
        "n_unassigned":   len(unassigned_keys),
        "n_dropped":      n_dropped,
        "wall_ms":        elapsed,
        "store_path":     latest_key,
        "versioned_path": versioned_key,
        "manifest_hash":  manifest_hash,
        "cache_hit":      False,
        "reorder_applied": reorder_applied,
        "plan":           plan,
    }
    await emit_progress(
        thread_id, "plan_write", "done",
        n_chapters=len(chapters), n_sources=n_sources_total,
        n_unassigned=len(unassigned_keys), n_dropped=n_dropped,
        wall_ms=elapsed,
    )
    logger.info(
        f"[plan_write] {slug}: {len(chapters)} chapters, "
        f"{n_sources_total} sources, {n_dropped} dropped, "
        f"{len(unassigned_keys)} unassigned; wrote {latest_key} + "
        f"{versioned_key} in {elapsed} ms"
    )
    return {"plan_path": latest_key, "plan_write_stats": stats,
            "status": "done"}
