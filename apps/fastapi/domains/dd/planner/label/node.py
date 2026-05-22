"""Substep 7 — label: cluster naming via bandit-routed big-LLM.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA research
(BERTopic-LLM defaults + TopicGPT arXiv 2311.01449 + Tutmaier 2025
arXiv 2502.18469 + Universal Self-Consistency arXiv 2311.17311 + LiSA
ACL 2025). Pipeline:

  1. Read cluster's soft-membership matrix + refine's reassigned
     cluster IDs.
  2. For each refined cluster, compute:
     - Top-20 c-TF-IDF keywords (reuse refine.py's helper)
     - Top-8 representative doc snippets (highest in-cluster soft
       membership; first 500 chars per doc)
  3. ROUND 1 — blind labeling: per-cluster prompt with keywords + rep
     docs, NO sibling labels. N=3 samples per cluster at temp=0.3,
     then Universal Self-Consistency vote (1 extra LLM call) picks
     the best. Unanimous samples skip USC.
  4. ROUND 2 — sibling-aware re-labeling: any cluster whose USC vote
     was NOT unanimous gets re-labeled with all round-1 labels in
     the "Existing labels in this corpus (DO NOT duplicate)" block.
  5. Noise cluster (-1) gets a hardcoded "Unclustered" — NEVER ask
     the LLM to name noise (Tutmaier 2025: hallucinated coherence).
  6. Persist labels + per-cluster decisions to MinIO as JSON.

State writes:
  cluster_labels_ref — MinIO key of the labels JSON blob
  label_stats        — observability dict (counts + bandit telemetry +
                       full label map for the UI)

Why these knobs (research-backed):
- Temp=0.3 (not 0): siblings collide on generic labels at temp=0
  (Tutmaier 2025, Stochastic Sandbox 2026).
- 8 rep docs (not 4): quality-over-speed sweet spot (Tutmaier 2025
  Approach 3 winner; BERTopic default is 4).
- Top-N by centroid (not diversity sampling): Tutmaier 2025 Approach
  4 consistently underperformed.
- First 500 chars per doc (not random middle): intro paragraph is the
  highest signal-density region for documentation pages.
- "Existing labels" block in round 2: MIT 2025 grammar-pattern study
  shows LLMs collide on shallow lexical patterns when blind to context.
- NOT batched ("label all 19 at once"): blows context window; quality
  drops past ~30K input on free-tier rotators (per research agent
  May 2026 brief).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from hashlib import sha256

import numpy as np

from ...ingestion.storage import get_storage

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from ..cluster import load_clusters
from ..refine import _compute_cluster_keywords, load_refine

from .constants import (
    _CONCURRENCY,
    _CTFIDF_DOC_CHARS,
    _KEYWORDS_TOP_K,
    _N_SAMPLES,
    _NOISE_LABEL,
    _PROMPT_VERSION,
    _REP_DOCS_PER_CLUSTER,
)
from .service import (
    _blob_key,
    _build_label_prompt,
    _label_one_cluster_usc,
    _pick_top_n_rep_docs,
    load_labels,
)


logger = logging.getLogger(__name__)


@traced("label")
async def label(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    refine_ref = state.get("refine_assignments_ref") or ""
    if not slug or not cluster_ref or not refine_ref:
        return {
            "cluster_labels_ref": "",
            "label_stats": {"skipped": "no_input", "wall_ms": 0,
                            "n_clusters": 0},
        }

    t0 = time.monotonic()

    # ── Cache fast-path ────────────────────────────────────────────────
    mh = sha256(
        (f"cluster={cluster_ref}|refine={refine_ref}|"
         f"v={_PROMPT_VERSION}|n={_N_SAMPLES}|"
         f"reps={_REP_DOCS_PER_CLUSTER}|"
         f"kw={_KEYWORDS_TOP_K}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    if await minio.exists(blob_key):
        try:
            blob = await minio.read_text(blob_key)
            cached = json.loads(blob)
            labels_dict = cached.get("labels") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_clusters":   len(labels_dict) - (
                    1 if "-1" in labels_dict else 0
                ),
                "n_round2":     cached.get("n_round2", 0),
                "wall_ms":      elapsed,
                "store_path":   blob_key,
                "cache_hit":    True,
                "n_samples":    _N_SAMPLES,
                "labels":       labels_dict,
                "prompt_version": cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "label", "done",
                n_clusters=stats["n_clusters"], n_round2=stats["n_round2"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[label] {slug}: CACHE HIT — {stats['n_clusters']} labels, "
                f"{elapsed} ms"
            )
            return {"cluster_labels_ref": blob_key, "label_stats": stats}
        except Exception as e:
            logger.warning(
                f"[label] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); recomputing"
            )

    await emit_progress(thread_id, "label", "start")

    # ── Load cluster + refine artifacts ────────────────────────────────
    cluster_blob = await minio.read_bytes(cluster_ref)
    cluster_keys, _orig_assigns, _max_probs, soft = load_clusters(cluster_blob)
    refine_blob = await minio.read_bytes(refine_ref)
    refine_keys, refined_assignments, _, _ = load_refine(refine_blob)

    if cluster_keys != refine_keys:
        raise RuntimeError(
            f"label: key mismatch — cluster has {len(cluster_keys)} keys, "
            f"refine has {len(refine_keys)}; pipeline integrity broken"
        )

    bodies = await minio.read_many(cluster_keys)
    unique_clusters = sorted({
        int(cid) for cid in refined_assignments if int(cid) >= 0
    })
    n_clusters = len(unique_clusters)

    if n_clusters == 0:
        elapsed = int((time.monotonic() - t0) * 1000)
        payload = {
            "labels": {"-1": _NOISE_LABEL},
            "n_round2": 0,
            "prompt_version": _PROMPT_VERSION,
            "round1_decisions": {},
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        stats = {
            "n_clusters":   0, "n_round2": 0, "wall_ms": elapsed,
            "store_path":   blob_key, "cache_hit": False,
            "skipped":      "no_clusters", "n_samples": _N_SAMPLES,
            "labels":       {"-1": _NOISE_LABEL},
        }
        await emit_progress(
            thread_id, "label", "done",
            n_clusters=0, n_round2=0, wall_ms=elapsed,
        )
        return {"cluster_labels_ref": blob_key, "label_stats": stats}

    # ── Per-cluster c-TF-IDF keywords + rep docs ───────────────────────
    cluster_docs_text: dict[int, str] = {}
    for cid in unique_clusters:
        cluster_mask_c = refined_assignments == cid
        idxs = np.where(cluster_mask_c)[0]
        if not len(idxs):
            continue
        cluster_docs_text[cid] = " ".join(
            (bodies[int(i)] or "")[:_CTFIDF_DOC_CHARS] for i in idxs
        )
    cluster_keywords = _compute_cluster_keywords(
        cluster_docs_text, top_k=_KEYWORDS_TOP_K,
    )
    cluster_rep_docs = {
        cid: _pick_top_n_rep_docs(
            cid, refined_assignments, soft, bodies,
            n=_REP_DOCS_PER_CLUSTER,
        )
        for cid in unique_clusters
    }

    await emit_progress(
        thread_id, "label", "context_prepared",
        n_clusters=n_clusters,
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    # ── Round 1: blind labeling (no sibling-aware context) ─────────────
    judged_done = {"n": 0, "unanimous": 0, "usc": 0, "err": 0,
                   "round": "round1"}
    _EMIT_EVERY = max(1, n_clusters // 20)

    async def _track_label(cid: int, existing: list[str]) -> dict:
        prompt = _build_label_prompt(
            cluster_keywords.get(cid, []),
            cluster_rep_docs.get(cid, []),
            existing,
        )
        result = await _label_one_cluster_usc(sem, cid, prompt)
        judged_done["n"] += 1
        if result.get("error"):
            judged_done["err"] += 1
        elif result["usc_vote"] == "unanimous":
            judged_done["unanimous"] += 1
        else:
            judged_done["usc"] += 1
        if (
            judged_done["n"] % _EMIT_EVERY == 0
            or judged_done["n"] == n_clusters
        ):
            await emit_progress(
                thread_id, "label", "llm_progress",
                judged=judged_done["n"], total=n_clusters,
                unanimous=judged_done["unanimous"],
                usc=judged_done["usc"], err=judged_done["err"],
                round=judged_done["round"],
            )
        return result

    round1_tasks = [_track_label(cid, []) for cid in unique_clusters]
    round1_results = await asyncio.gather(*round1_tasks)

    labels: dict[int, str] = {}
    round1_decisions: dict[int, dict] = {}
    for r in round1_results:
        labels[r["cluster_id"]] = r["label"]
        round1_decisions[r["cluster_id"]] = r

    # ── Round 2: re-label USC-split clusters with sibling-aware context ─
    split_cids = [
        r["cluster_id"] for r in round1_results
        if r["usc_vote"] in (
            "usc_voted", "mode_fallback", "no_valid_samples",
        )
    ]
    n_round2 = 0
    if split_cids:
        judged_done["n"] = 0
        judged_done["unanimous"] = 0
        judged_done["usc"] = 0
        judged_done["err"] = 0
        judged_done["round"] = "round2"
        await emit_progress(
            thread_id, "label", "round2_start",
            n_round2=len(split_cids),
        )
        round2_tasks = []
        for cid in split_cids:
            existing = [v for k, v in labels.items() if k != cid]
            round2_tasks.append(_track_label(cid, existing))
        round2_results = await asyncio.gather(*round2_tasks)
        for r in round2_results:
            labels[r["cluster_id"]] = r["label"]
        n_round2 = len(round2_results)

    labels[-1] = _NOISE_LABEL

    # ── Persist to MinIO (JSON, labels are small dicts) ────────────────
    payload = {
        "labels": {str(k): v for k, v in labels.items()},
        "n_round2": n_round2,
        "prompt_version": _PROMPT_VERSION,
        "round1_decisions": {
            str(k): {
                "label":    v["label"],
                "usc_vote": v["usc_vote"],
                "samples":  v["samples"],
                "error":    v.get("error"),
            }
            for k, v in round1_decisions.items()
        },
    }
    await minio.write(
        blob_key, json.dumps(payload), content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    n_unanimous = sum(
        1 for r in round1_results if r["usc_vote"] == "unanimous"
    )
    n_usc_voted = sum(
        1 for r in round1_results if r["usc_vote"] == "usc_voted"
    )
    n_errors = sum(1 for r in round1_results if r.get("error"))

    # Bandit deployment-usage tally (which models actually answered)
    dep_usage: dict[str, int] = {}
    for r in round1_results:
        for m in (r.get("metas") or []):
            dep = (m or {}).get("deployment") or "?"
            dep_usage[dep] = dep_usage.get(dep, 0) + 1
    deployment_summary = [
        {"deployment": dep, "calls": n}
        for dep, n in sorted(dep_usage.items(), key=lambda kv: -kv[1])
    ]

    stats = {
        "n_clusters":       n_clusters,
        "n_unanimous":      n_unanimous,
        "n_usc_voted":      n_usc_voted,
        "n_round2":         n_round2,
        "n_errors":         n_errors,
        "wall_ms":          elapsed,
        "store_path":       blob_key,
        "cache_hit":        False,
        "n_samples":        _N_SAMPLES,
        "labels":           {str(k): v for k, v in labels.items()},
        "deployment_usage": deployment_summary,
        "prompt_version":   _PROMPT_VERSION,
    }

    await emit_progress(
        thread_id, "label", "done",
        n_clusters=n_clusters, n_round2=n_round2, wall_ms=elapsed,
    )
    logger.info(
        f"[label] {slug}: {n_clusters} clusters labeled, "
        f"{n_unanimous} unanimous, {n_usc_voted} USC-voted, "
        f"{n_round2} round-2 re-labels, {n_errors} errors; {elapsed} ms"
    )
    return {"cluster_labels_ref": blob_key, "label_stats": stats}
