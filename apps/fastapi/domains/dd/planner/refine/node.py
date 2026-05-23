"""Substep 6 — refine: LITA boundary-doc reassignment via bandit LLM.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA research
(LITA arXiv 2412.12459, "None of the Above" ACL 2025, position-bias
IJCNLP 2025, Wharton CoT 2025, k-LLMmeans arXiv 2502.09667). Pipeline:

  1. Read cluster's soft-membership matrix (N×K) + assignments.
  2. Identify boundary docs where max_prob < _BOUNDARY_FLOOR (0.60).
  3. Compute per-cluster context — top-7 c-TF-IDF keywords + 1
     representative-doc snippet (chosen as the doc with highest
     in-cluster soft membership).
  4. For each boundary doc: take the top-K=5 candidate clusters from
     the soft matrix, shuffle letter labels A-E (defeats primacy bias),
     build a strict JSON-output prompt, call the ParetoBandit-routed
     big-LLM via `chat_judge_bandit_async`, parse the verdict, map the
     letter back to the original cluster_id.
  5. Allow `null` response — boundary docs that fit no candidate stay
     as noise (-1). Per ACL 2025 NOTA paper: forcing a pick drops
     accuracy 30-50%.
  6. Persist refined assignments + per-doc decisions to MinIO.

State writes:
  refine_assignments_ref — MinIO key of the .npz blob
  refine_stats           — observability dict (counts + bandit telemetry)

The .npz holds: keys (N), refined_assignments (N), original_assignments
(N), decisions_json (list of dicts with doc_idx + verdict + meta).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from hashlib import sha256

import numpy as np

from ...ingestion.storage import get_storage

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from ..cluster import load_clusters

from .constants import (
    _BOUNDARY_FLOOR,
    _CTFIDF_DOC_CHARS,
    _GMM_POSTERIOR_THRESHOLD,
    _PROMPT_VERSION,
    _REFINE_CONCURRENCY,
    _TOP_K,
    _KEYWORDS_PER_CLUSTER,
)
from .service import (
    _blob_key,
    _compute_cluster_keywords,
    _pack_npz,
    _pick_representative_doc,
    _refine_one,
    load_refine,
    softmax_resolve_boundary,
)


logger = logging.getLogger(__name__)


def _gmm_mode_active() -> bool:
    """Phase D (2026-05-23): KD_REFINE_USE_GMM=1 enables the deterministic
    soft-membership boundary resolver fast-path. Default off — set the env
    per pod to enable. Cuts LLM-judge cost ~85% on LangChain-scale corpora
    with ~3-5pp accuracy regression."""
    return os.environ.get("KD_REFINE_USE_GMM", "0") == "1"


@traced("refine")
async def refine(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    if not slug or not cluster_ref:
        return {
            "refine_assignments_ref": "",
            "refine_stats": {"skipped": "no input", "n_docs": 0, "wall_ms": 0},
        }

    t0 = time.monotonic()

    # ── Cache fast-path ────────────────────────────────────────────────
    # Hash includes cluster_ref (itself content-addressed), threshold,
    # top-K, keyword count, prompt version. Any of these change → cache
    # invalidates cleanly.
    mh = sha256(
        (f"cluster={cluster_ref}|floor={_BOUNDARY_FLOOR}|"
         f"topk={_TOP_K}|kw={_KEYWORDS_PER_CLUSTER}|"
         f"v={_PROMPT_VERSION}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    if await minio.exists(blob_key):
        try:
            blob = await minio.read_bytes(blob_key)
            cached_keys, refined, original, decisions = load_refine(blob)
            n_changed = int((refined != original).sum())
            n_null = sum(
                1 for d in decisions
                if int(d.get("new_cluster_id", -2)) == -1
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_docs":         int(len(cached_keys)),
                "n_boundary":     len(decisions),
                "n_changed":      n_changed,
                "n_null":         n_null,
                "n_errors":       sum(1 for d in decisions if d.get("error")),
                "wall_ms":        elapsed,
                "store_path":     blob_key,
                "boundary_floor": _BOUNDARY_FLOOR,
                "top_k":          _TOP_K,
                "cache_hit":      True,
            }
            await emit_progress(
                thread_id, "refine", "done",
                n_docs=int(len(cached_keys)), n_boundary=len(decisions),
                n_changed=n_changed, n_null=n_null, wall_ms=elapsed,
                cache_hit=True,
            )
            logger.info(
                f"[refine] {slug}: CACHE HIT — {len(decisions)} boundary docs, "
                f"{n_changed} reassigned, {n_null} null, {elapsed} ms"
            )
            return {"refine_assignments_ref": blob_key,
                    "refine_stats": stats}
        except Exception as e:
            logger.warning(
                f"[refine] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); recomputing"
            )

    await emit_progress(thread_id, "refine", "start")

    # ── Load cluster artifacts ─────────────────────────────────────────
    cluster_blob = await minio.read_bytes(cluster_ref)
    cluster_keys, assignments, max_probs, soft = load_clusters(cluster_blob)
    n_docs = len(cluster_keys)
    K = soft.shape[1] if soft.ndim == 2 else 0

    # Identify boundary docs.
    boundary_mask = max_probs < _BOUNDARY_FLOOR
    boundary_indices = np.where(boundary_mask)[0]
    n_boundary = int(boundary_indices.size)

    if n_boundary == 0 or K == 0:
        # Nothing to refine — persist as-is so downstream gets consistent state.
        blob = _pack_npz(cluster_keys, assignments, assignments.copy(), [])
        await minio.write(
            blob_key, blob, content_type="application/octet-stream",
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        stats = {
            "n_docs": n_docs, "n_boundary": 0, "n_changed": 0,
            "n_null": 0, "n_errors": 0,
            "wall_ms": elapsed, "store_path": blob_key,
            "boundary_floor": _BOUNDARY_FLOOR, "top_k": _TOP_K,
            "cache_hit": False, "skipped": "no_boundary_docs",
        }
        await emit_progress(
            thread_id, "refine", "done",
            n_docs=n_docs, n_boundary=0, n_changed=0, n_null=0,
            wall_ms=elapsed,
        )
        return {"refine_assignments_ref": blob_key, "refine_stats": stats}

    # ── Load doc bodies (needed for c-TF-IDF + rep snippets + judge prompts) ─
    bodies = await minio.read_many(cluster_keys)
    await emit_progress(
        thread_id, "refine", "context_prepared",
        n_docs=n_docs, n_boundary=n_boundary, n_clusters=K,
    )

    # ── Per-cluster context: c-TF-IDF keywords + rep snippets ──────────
    cluster_docs_text: dict[int, str] = {}
    for cid in range(K):
        cluster_mask_c = assignments == cid
        if not cluster_mask_c.any():
            continue
        cluster_indices = np.where(cluster_mask_c)[0]
        cluster_docs_text[cid] = " ".join(
            (bodies[i] or "")[:_CTFIDF_DOC_CHARS]
            for i in cluster_indices
        )
    cluster_keywords = _compute_cluster_keywords(cluster_docs_text)
    cluster_snippets: dict[int, str] = {
        cid: _pick_representative_doc(cid, assignments, soft, bodies)
        for cid in cluster_docs_text.keys()
    }

    # ── Phase D fast-path: deterministic soft-membership resolver ──────
    # When KD_REFINE_USE_GMM=1, sharpen the soft membership distribution and
    # take the deterministic argmax for boundary docs whose sharpened max
    # posterior crosses _GMM_POSTERIOR_THRESHOLD. Fall back to LLM-judge ONLY
    # for the genuinely-uncertain residual. ~85% LLM-cost reduction at
    # LangChain scale with ~3-5pp boundary-assignment accuracy regression.
    gmm_used = _gmm_mode_active()
    gmm_assignments_for_boundary: np.ndarray | None = None
    gmm_posteriors_for_boundary: np.ndarray | None = None
    gmm_confident_mask: np.ndarray | None = None
    if gmm_used:
        valid_cluster_ids = set(cluster_keywords.keys())
        (
            gmm_assignments_for_boundary,
            gmm_posteriors_for_boundary,
            gmm_confident_mask,
        ) = softmax_resolve_boundary(
            soft=soft,
            boundary_indices=boundary_indices,
            valid_cluster_ids=valid_cluster_ids,
        )
        n_confident = int(gmm_confident_mask.sum())
        await emit_progress(
            thread_id, "refine", "gmm_resolved",
            total_boundary=n_boundary,
            n_confident=n_confident,
            n_residual=n_boundary - n_confident,
            threshold=_GMM_POSTERIOR_THRESHOLD,
        )
        logger.info(
            f"[refine] {slug}: GMM fast-path resolved {n_confident}/{n_boundary} "
            f"({n_confident * 100 // max(n_boundary, 1)}%) boundary docs "
            f"deterministically (posterior ≥ {_GMM_POSTERIOR_THRESHOLD}); "
            f"LLM-judge will only run on the {n_boundary - n_confident} residual"
        )

    # ── Refine loop ────────────────────────────────────────────────────
    sem = asyncio.Semaphore(_REFINE_CONCURRENCY)
    judged_done = {"n": 0, "changed": 0, "null": 0, "err": 0}
    _EMIT_EVERY = max(1, n_boundary // 40)

    async def _track(i: int, body: str, candidates: list[int]) -> dict:
        result = await _refine_one(
            sem, i, body, candidates,
            cluster_keywords, cluster_snippets,
            int(assignments[i]),
        )
        judged_done["n"] += 1
        if result.get("error"):
            judged_done["err"] += 1
        if int(result.get("new_cluster_id", -2)) == -1:
            judged_done["null"] += 1
        if int(result.get("new_cluster_id", -2)) != int(assignments[i]):
            judged_done["changed"] += 1
        if (
            judged_done["n"] % _EMIT_EVERY == 0
            or judged_done["n"] == n_boundary
        ):
            await emit_progress(
                thread_id, "refine", "llm_progress",
                judged=judged_done["n"], total=n_boundary,
                changed=judged_done["changed"],
                null=judged_done["null"],
                err=judged_done["err"],
            )
        return result

    # Per-boundary-doc decision: either via GMM fast-path (cheap, deterministic)
    # or via LLM-judge (slow, contextual). Build both lists then merge.
    boundary_idx_list = boundary_indices.tolist()
    deterministic_decisions: list[dict] = []
    llm_task_specs: list[tuple[int, str, list[int]]] = []
    for pos, i in enumerate(boundary_idx_list):
        # Top-K candidate cluster_ids for this doc, sorted by soft membership.
        # Exclude clusters with no docs (cluster_keywords doesn't have them).
        sorted_cids = np.argsort(-soft[int(i)])
        candidates = [
            int(cid) for cid in sorted_cids
            if int(cid) in cluster_keywords
        ][:_TOP_K]
        # GMM fast-path: take the deterministic assignment when confident.
        if (
            gmm_used
            and gmm_confident_mask is not None
            and bool(gmm_confident_mask[pos])
        ):
            deterministic_decisions.append({
                "doc_idx":        int(i),
                "new_cluster_id": int(gmm_assignments_for_boundary[pos]),
                "confidence":     float(gmm_posteriors_for_boundary[pos]),
                "rationale":      (
                    f"GMM softmax-sharpened posterior "
                    f"{float(gmm_posteriors_for_boundary[pos]):.3f} ≥ "
                    f"{_GMM_POSTERIOR_THRESHOLD} threshold"
                ),
                "meta":           {
                    "deployment": "gmm/softmax-sharpened",
                    "latency_s":  0.0,
                },
                "error":          None,
            })
        else:
            llm_task_specs.append((int(i), bodies[int(i)], candidates))
    # Fire LLM-judge ONLY for the residual (or all of them when GMM mode is off).
    tasks = [
        _track(spec_i, spec_body, spec_cands)
        for spec_i, spec_body, spec_cands in llm_task_specs
    ]
    llm_decisions = await asyncio.gather(*tasks) if tasks else []
    decisions = deterministic_decisions + list(llm_decisions)

    # ── Build refined assignments ──────────────────────────────────────
    refined = assignments.copy()
    for d in decisions:
        idx = int(d["doc_idx"])
        new_cid = int(d.get("new_cluster_id", refined[idx]))
        refined[idx] = new_cid

    n_changed = int((refined != assignments).sum())
    n_null = sum(
        1 for d in decisions if int(d.get("new_cluster_id", -2)) == -1
    )
    n_errors = sum(1 for d in decisions if d.get("error"))

    # ── Persist to MinIO ───────────────────────────────────────────────
    # Strip heavy meta keys; keep what UI / debug needs.
    decisions_for_blob = [
        {
            "doc_idx":       d["doc_idx"],
            "new_cluster_id": d["new_cluster_id"],
            "confidence":    d.get("confidence"),
            "rationale":     d.get("rationale"),
            "deployment":    (d.get("meta") or {}).get("deployment"),
            "latency_s":     (d.get("meta") or {}).get("latency_s"),
            "error":         d.get("error"),
        }
        for d in decisions
    ]
    blob = _pack_npz(
        cluster_keys, refined, assignments, decisions_for_blob,
    )
    await minio.write(blob_key, blob, content_type="application/octet-stream")

    # ── Bandit deployment usage tally (which models actually answered) ─
    dep_usage: dict[str, int] = {}
    for d in decisions:
        dep = (d.get("meta") or {}).get("deployment") or "?"
        dep_usage[dep] = dep_usage.get(dep, 0) + 1
    deployment_summary = [
        {"deployment": dep, "calls": n}
        for dep, n in sorted(dep_usage.items(), key=lambda kv: -kv[1])
    ]

    elapsed = int((time.monotonic() - t0) * 1000)
    n_via_gmm = len(deterministic_decisions)
    n_via_llm = n_boundary - n_via_gmm
    stats = {
        "n_docs":           n_docs,
        "n_boundary":       n_boundary,
        "n_changed":        n_changed,
        "n_null":           n_null,
        "n_errors":         n_errors,
        "wall_ms":          elapsed,
        "store_path":       blob_key,
        "boundary_floor":   _BOUNDARY_FLOOR,
        "top_k":            _TOP_K,
        "cache_hit":        False,
        "blob_bytes":       len(blob),
        "deployment_usage": deployment_summary,
        "prompt_version":   _PROMPT_VERSION,
        # Phase D telemetry
        "mode":             "gmm+llm" if gmm_used else "llm_judge",
        "n_via_gmm":        n_via_gmm,
        "n_via_llm":        n_via_llm,
        "gmm_threshold":    _GMM_POSTERIOR_THRESHOLD if gmm_used else None,
    }

    await emit_progress(
        thread_id, "refine", "done",
        n_docs=n_docs, n_boundary=n_boundary,
        n_changed=n_changed, n_null=n_null, wall_ms=elapsed,
    )
    logger.info(
        f"[refine] {slug}: {n_boundary} boundary docs judged, "
        f"{n_changed} reassigned, {n_null} sent to noise, "
        f"{n_errors} errors; {elapsed} ms"
    )
    return {"refine_assignments_ref": blob_key, "refine_stats": stats}
