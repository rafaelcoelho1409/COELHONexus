"""Substep 5 — cluster: dim-reduce + density-cluster the relevant corpus.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA research
(see project memory), the canonical stack is:

    UMAP(n_components=10, metric='cosine', n_neighbors=15, min_dist=0.0,
         random_state=42)
      → HDBSCAN(min_cluster_size=8, min_samples=5,
                cluster_selection_method='eom', prediction_data=True)
      → all_points_membership_vectors()    # N×K soft matrix

Why this exact recipe (justifications from the research agent's report):

- `umap-learn` lib: stable, lmcinnes-maintained, 2026 active. n_components=10
  is the clustering sweet-spot (curse-of-dim hurts HDBSCAN density
  estimates at >50-D). `min_dist=0.0` produces "clumpy" embeddings —
  explicit lmcinnes recommendation when clustering is downstream
  (visualization wants spread, clustering wants clumps). `metric='cosine'`
  matches our L2-normalized embedding geometry.

- `scikit-learn-contrib/hdbscan` lib (NOT sklearn.cluster.HDBSCAN — the
  sklearn-bundled version is ~12× slower AND missing the
  `all_points_membership_vectors()` API). `cluster_selection_method='eom'`
  gives broader persistence-weighted clusters suitable for chapter
  candidates ('leaf' over-fragments). `min_cluster_size=8` lands ~10-30
  clusters on 100-2000 docs (a 1000-doc corpus → ~12-25 clusters); the
  downstream `reduce` LLM merges those to 4-12 chapters.

- `all_points_membership_vectors()` returns N×K soft memberships — REQUIRED
  for LITA refine (`refine` node will pick boundary docs where
  `max_prob < 0.5` AND offer alternative cluster choices to the LLM).
  HDBSCAN's `.probabilities_` is a scalar per point (confidence in the
  assigned label only) — insufficient for boundary handling.

State writes:
  cluster_assignments_ref — MinIO key of the .npz blob (cluster_id + max_prob per key)
  cluster_stats           — observability dict (count / sizes / noise / boundary / wall)

The N×K soft matrix is kept in MinIO (potentially large) — state carries
only the pointer, matching the embed_corpus pattern.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time

import numpy as np

from services.docs_distiller.ingestion.storage_minio import get_storage

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from .embed_corpus import load_embeddings


logger = logging.getLogger(__name__)


# Hyperparameters — research-recommended defaults. Tuned for the
# 100-2000-doc / variable-density topical-cluster scenario.
_UMAP_DIM            = 10
_UMAP_N_NEIGHBORS    = 15
_UMAP_MIN_DIST       = 0.0
_HDBSCAN_MIN_SAMPLES = 5
# A boundary doc has max-prob below this floor; LITA's `refine` node
# will re-evaluate those via LLM-small reassignment to the best cluster.
_BOUNDARY_PROB_FLOOR = 0.5
_BLOB_PREFIX         = "planner"
# Cache schema version — bump on hyperparameter formula change so old
# blobs invalidate cleanly.
#   v2 (2026-05-18 AM): linear adaptive min_cluster_size — backfired at
#                       large scale (langchain 744 docs → mcs=49 → mega-
#                       cluster collapse, 19→4 clusters).
#   v3 (2026-05-18 PM): sqrt-capped formula per May-2026 SOTA research.
import math

_CACHE_VERSION       = "v3"


def _adaptive_min_cluster_size(n_docs: int) -> int:
    """HDBSCAN's `min_cluster_size` is the density-mode floor — a cluster
    must contain at least this many points to be recognized at all. The
    parameter is fundamentally about density geometry, NOT corpus size,
    so naive linear scaling violates HDBSCAN's invariants on large
    corpora (`N/15` at N=744 demands a density mode of 49 points, which
    in 10-D UMAP space doesn't exist for narrow sub-topics → they get
    absorbed into mega-clusters; the langchain stack 19→4 collapse).

    The May-2026 SOTA pattern (BERTopic, LITA, TnT-LLM, Clio, HERCULES)
    is: use a small bounded min_cluster_size (deliberately over-fragment)
    + push granularity decisions to the downstream LLM-merge step. That
    second knob is `_TARGET_K` in `reduce.py`, NOT this value.

    Formula: `max(5, min(15, ceil(sqrt(N)/3)))` per the BERTopic "1-2%
    of N ≈ sqrt(N)" rule of thumb, capped 5-15. Concrete sizing:
      -   85 docs (pydantic)        → 5
      -  250 docs (terragrunt-class) → 5
      -  744 docs (langchain stack) → 9    (vs broken linear's 49)
      - 1500 docs (docker-class)    → 13
      - 3000 docs                   → 15   (cap binds)

    Floor 5 = HDBSCAN-recommended minimum for meaningful density modes
    on real-world text embeddings (below 5, every micro-cluster looks
    like a density mode + outlier rate explodes).
    Cap 15 = empirical safe ceiling — beyond this, narrow sub-topics
    get absorbed into broader modes (the langchain failure mode).

    Sources:
      - LLM-Assisted Topic Reduction for BERTopic (arXiv 2509.19365)
      - BERTopic 2026 parameter-tuning docs
      - DBOpt / Nature Comm Biology 2025 (Bayesian-opt alternative,
        deferred — overkill for 85-3000 corpus range)
      - See docs/PLANNER-IMPROVEMENTS-BACKLOG-2026-05-18.md option #1
        + the May-2026 SOTA-research transcript that drove the v2→v3
        switch.
    """
    return max(5, min(15, math.ceil(math.sqrt(n_docs) / 3)))


def _blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/clusters/{manifest_hash}.npz"


def _pack_npz(
    keys: list[str],
    assignments: np.ndarray,
    probabilities: np.ndarray,
    soft_membership: np.ndarray,
) -> bytes:
    """Serialize cluster artifacts to a compressed .npz byte blob.
    `assignments` is int (cluster id, -1 = noise); `probabilities` is
    float32 max-prob per point; `soft_membership` is the N×K matrix
    used by LITA refine to see alternative cluster candidates."""
    arr_keys = np.array(keys, dtype=object)
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        keys=arr_keys,
        assignments=assignments.astype(np.int32),
        probabilities=probabilities.astype(np.float32),
        soft_membership=soft_membership.astype(np.float32),
    )
    return buf.getvalue()


def load_clusters(blob_bytes: bytes) -> tuple[
    list[str], np.ndarray, np.ndarray, np.ndarray,
]:
    """Inverse of _pack_npz. Returned for use by downstream nodes
    (refine, label, reduce) so they share one decoder. Returns
    (keys, assignments, max_probs, soft_membership)."""
    buf = io.BytesIO(blob_bytes)
    with np.load(buf, allow_pickle=True) as data:
        keys = [str(k) for k in data["keys"].tolist()]
        assignments = np.asarray(data["assignments"], dtype=np.int32)
        probabilities = np.asarray(data["probabilities"], dtype=np.float32)
        soft = np.asarray(data["soft_membership"], dtype=np.float32)
    return keys, assignments, probabilities, soft


@traced("cluster")
async def cluster(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = state.get("relevant_files") or []
    embeddings_ref = state.get("embeddings_ref") or ""
    if not slug or not relevant_files:
        return {
            "cluster_assignments_ref": "",
            "cluster_stats": {
                "n_clusters": 0, "n_docs": 0, "skipped": "no input",
                "wall_ms": 0,
            },
        }
    if not embeddings_ref:
        raise RuntimeError(
            "cluster: missing embeddings_ref in state — embed_corpus must "
            "run first"
        )

    t0 = time.monotonic()
    await emit_progress(
        thread_id, "cluster", "start",
        n_docs=len(relevant_files),
    )

    minio = get_storage()

    # Adaptive HDBSCAN floor — scales with corpus size so small frameworks
    # produce meaningful cluster counts (see _adaptive_min_cluster_size).
    min_cluster_size = _adaptive_min_cluster_size(len(relevant_files))

    # ── Cache fast-path ───────────────────────────────────────────────
    # Hash key parts (must include hyperparams that affect output so a
    # config change invalidates the blob). Same {slug}/clusters/{hash}.npz
    # layout as the cold path below. `_CACHE_VERSION` invalidates v1
    # blobs (computed when min_cluster_size was hardcoded 8).
    from hashlib import sha256
    mh = sha256(
        ("|".join(sorted(relevant_files)) +
         f"|umap{_UMAP_DIM}|hdbscan{min_cluster_size}"
         f"|{_CACHE_VERSION}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    if await minio.exists(blob_key):
        try:
            blob = await minio.read_bytes(blob_key)
            cached_keys, cached_assignments, cached_probs, cached_soft = \
                load_clusters(blob)
            unique = np.unique(cached_assignments)
            cluster_sizes = [
                int(np.sum(cached_assignments == cid))
                for cid in unique if cid != -1
            ]
            cluster_sizes.sort(reverse=True)
            n_noise = int(np.sum(cached_assignments == -1))
            n_clusters = int(len(cluster_sizes))
            n_boundary = int(np.sum(cached_probs < _BOUNDARY_PROB_FLOOR))
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_clusters":       n_clusters,
                "n_noise":          n_noise,
                "n_boundary":       n_boundary,
                "n_docs":           int(len(cached_keys)),
                "wall_ms":          elapsed,
                "store_path":       blob_key,
                "cluster_sizes":    cluster_sizes[:30],
                "boundary_floor":   _BOUNDARY_PROB_FLOOR,
                "umap_dim":         _UMAP_DIM,
                "min_cluster_size": min_cluster_size,
                "blob_bytes":       len(blob),
                "cache_hit":        True,
            }
            _attach_otel_attrs(stats)
            logger.info(
                f"[cluster] {slug}: CACHE HIT — {n_clusters} clusters, "
                f"{n_noise} noise, {n_boundary} boundary, {elapsed} ms"
            )
            await emit_progress(
                thread_id, "cluster", "done",
                n_clusters=n_clusters, n_noise=n_noise, n_boundary=n_boundary,
                n_docs=int(len(cached_keys)), wall_ms=elapsed, cache_hit=True,
            )
            return {"cluster_assignments_ref": blob_key,
                    "cluster_stats": stats}
        except Exception as e:
            logger.warning(
                f"[cluster] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); recomputing"
            )

    # ── Load vectors + filter to relevant subset (post-off_topic). ────
    embed_blob = await minio.read_bytes(embeddings_ref)
    stored_keys, page_vecs = load_embeddings(embed_blob)
    key_to_idx = {k: i for i, k in enumerate(stored_keys)}
    missing = [k for k in relevant_files if k not in key_to_idx]
    if missing:
        raise RuntimeError(
            f"cluster: {len(missing)} relevant files have no matching "
            f"vector in {embeddings_ref!r} — re-run embed_corpus "
            f"(first missing: {missing[0]!r})"
        )
    indices = np.array([key_to_idx[k] for k in relevant_files], dtype=np.int64)
    X = page_vecs[indices]   # (N, D); already L2-normalized by embed_corpus

    n_docs = X.shape[0]
    # Guardrails: degenerate corpora can't be UMAP'd. If N is too small to
    # produce meaningful clusters, return a single-cluster assignment and
    # let the operator see the warning in the stats.
    min_for_clustering = max(min_cluster_size * 2, _UMAP_N_NEIGHBORS + 1)
    if n_docs < min_for_clustering:
        elapsed = int((time.monotonic() - t0) * 1000)
        assignments = np.zeros(n_docs, dtype=np.int32)
        probabilities = np.ones(n_docs, dtype=np.float32)
        soft = np.ones((n_docs, 1), dtype=np.float32)
        blob = _pack_npz(relevant_files, assignments, probabilities, soft)
        # Hash purely off the file list so reuse on the same corpus hits
        # this fast path again.
        from hashlib import sha256
        mh = sha256(
            ("|".join(sorted(relevant_files))).encode("utf-8"),
        ).hexdigest()[:16]
        blob_key = _blob_key(slug, mh)
        await minio.write(blob_key, blob, content_type="application/octet-stream")
        stats = {
            "n_clusters":     1,
            "n_noise":        0,
            "n_boundary":     0,
            "n_docs":         n_docs,
            "wall_ms":        elapsed,
            "store_path":     blob_key,
            "cluster_sizes":  [int(n_docs)],
            "fallback":       "small_corpus",
            "min_required":   min_for_clustering,
        }
        await emit_progress(
            thread_id, "cluster", "done",
            n_clusters=1, n_noise=0, n_boundary=0,
            n_docs=n_docs, wall_ms=elapsed, fallback="small_corpus",
        )
        logger.info(
            f"[cluster] {slug}: SMALL CORPUS ({n_docs} < {min_for_clustering}) "
            f"— single-cluster fallback, {elapsed} ms"
        )
        return {"cluster_assignments_ref": blob_key, "cluster_stats": stats}

    # ── UMAP dim reduction ─────────────────────────────────────────────
    import umap   # lazy import — pulls in numba JIT compilation on first call
    await emit_progress(
        thread_id, "cluster", "umap_start",
        n_docs=n_docs, in_dim=int(X.shape[1]), out_dim=_UMAP_DIM,
    )
    reducer = umap.UMAP(
        n_components=_UMAP_DIM,
        metric="cosine",
        n_neighbors=min(_UMAP_N_NEIGHBORS, max(2, n_docs - 1)),
        min_dist=_UMAP_MIN_DIST,
        random_state=42,
        n_jobs=1,   # required for deterministic output when random_state is set
    )
    # UMAP.fit_transform is synchronous CPU-bound work (multi-second on
    # cold-numba first call). asyncio.to_thread offloads it so the event
    # loop stays responsive — health checks, SSE flushes, cancel watcher
    # all continue running while the math executes.
    X_reduced = await asyncio.to_thread(reducer.fit_transform, X)
    logger.info(
        f"[cluster] {slug}: UMAP done — {X_reduced.shape}, "
        f"{int((time.monotonic() - t0) * 1000)} ms cumulative"
    )

    # ── HDBSCAN density clustering + soft membership ───────────────────
    import hdbscan
    await emit_progress(
        thread_id, "cluster", "hdbscan_start",
        n_docs=n_docs, reduced_dim=int(X_reduced.shape[1]),
    )
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=_HDBSCAN_MIN_SAMPLES,
        cluster_selection_method="eom",
        prediction_data=True,
        core_dist_n_jobs=1,
    )
    # Same reason as UMAP — keep the event loop responsive during the
    # synchronous fit_predict + soft-membership computation.
    assignments = await asyncio.to_thread(clusterer.fit_predict, X_reduced)
    # Soft membership — N×K matrix; rows for noise points have low values
    # across all clusters. K = number of non-noise clusters HDBSCAN found.
    soft = await asyncio.to_thread(
        hdbscan.all_points_membership_vectors, clusterer,
    )
    logger.info(
        f"[cluster] {slug}: HDBSCAN done — {len(np.unique(assignments))} groups, "
        f"{int((time.monotonic() - t0) * 1000)} ms cumulative"
    )
    if soft.ndim == 1:
        # Degenerate: HDBSCAN found only one cluster (or all noise). Wrap
        # to keep the (N, K) shape downstream code expects.
        soft = soft.reshape(-1, 1)
    if soft.shape[1] == 0:
        # All-noise case — give every point a single dummy "noise" cluster
        # so refine has SOMETHING to grade against.
        soft = np.ones((n_docs, 1), dtype=np.float32) * 0.0

    # Max probability per doc (used to identify boundary docs).
    max_probs = (
        soft.max(axis=1)
        if soft.shape[1] > 0
        else np.zeros(n_docs, dtype=np.float32)
    )

    # ── Stats ───────────────────────────────────────────────────────────
    unique, counts = np.unique(assignments, return_counts=True)
    cluster_sizes = {
        int(cid): int(n) for cid, n in zip(unique, counts) if cid != -1
    }
    n_noise = int(np.sum(assignments == -1))
    n_clusters = len(cluster_sizes)
    n_boundary = int(np.sum(max_probs < _BOUNDARY_PROB_FLOOR))
    size_list = sorted(cluster_sizes.values(), reverse=True)

    # ── Persist to MinIO ────────────────────────────────────────────────
    # `blob_key` was already computed at the top of the cache-check;
    # reuse to keep the hash deterministic across the two code paths.
    blob = _pack_npz(relevant_files, assignments, max_probs, soft)
    await minio.write(blob_key, blob, content_type="application/octet-stream")

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_clusters":         n_clusters,
        "n_noise":            n_noise,
        "n_boundary":         n_boundary,
        "n_docs":             n_docs,
        "wall_ms":            elapsed,
        "store_path":         blob_key,
        "cluster_sizes":      size_list[:30],   # cap for state payload size
        "boundary_floor":     _BOUNDARY_PROB_FLOOR,
        "umap_dim":           _UMAP_DIM,
        "min_cluster_size":   min_cluster_size,
        "blob_bytes":         len(blob),
        "cache_hit":          False,
    }
    _attach_otel_attrs(stats)
    logger.info(
        f"[cluster] {slug}: {n_clusters} clusters, {n_noise} noise, "
        f"{n_boundary} boundary docs (max_prob < {_BOUNDARY_PROB_FLOOR}); "
        f"{elapsed} ms; blob={len(blob) // 1024} KB"
    )
    await emit_progress(
        thread_id, "cluster", "done",
        n_clusters=n_clusters, n_noise=n_noise, n_boundary=n_boundary,
        n_docs=n_docs, wall_ms=elapsed,
    )
    return {"cluster_assignments_ref": blob_key, "cluster_stats": stats}


def _attach_otel_attrs(stats: dict) -> None:
    try:
        from opentelemetry import trace as _otel_trace
        span = _otel_trace.get_current_span()
        for k, v in stats.items():
            if v is None or isinstance(v, (list, dict)):
                continue
            span.set_attribute(f"cluster.{k}", v)
    except Exception:
        pass
