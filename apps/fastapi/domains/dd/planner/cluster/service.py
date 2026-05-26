from __future__ import annotations

import io
import math

import numpy as np

from .constants import (
    _BLOB_PREFIX,
    _BOUNDARY_PROB_FLOOR,
    _CACHE_VERSION,
    _UMAP_N_NEIGHBORS_CAP,
)


def _adaptive_n_neighbors(n_docs: int) -> int:
    """UMAP `n_neighbors` adaptive to corpus size (2026-05-26).

    The fixed `n_neighbors=30` shipped in Bundle 5a (2026-05-25) is correct
    for medium-to-large corpora (≥120 docs) — it biases UMAP toward global
    topical structure, which is what density-based HDBSCAN downstream wants.
    But for TINY corpora (Browser Use N=38), 30 ≈ 80% of the corpus → UMAP's
    k-NN graph collapses into a near-complete graph, destroying local density
    information → HDBSCAN sees a featureless manifold → labels everything
    noise → planner returns 0 chapters.

    Empirical evidence:
      - Browser Use (N=38, n_neighbors=30): 0 clusters, 38/38 noise
      - Claude Code (N=126, n_neighbors=30): 6 clusters (healthy)
      - FastMCP    (N=335, n_neighbors=30): 14 clusters (healthy)

    Adaptive formula: `min(_UMAP_N_NEIGHBORS_CAP, max(8, n_docs // 4))`.
    Concrete sizing under cap=30:
      -   38 docs (Browser Use)  → 9      (was 30 — broken)
      -   90 docs                → 22
      -  126 docs (Claude Code)  → 30     (cap binds; unchanged)
      -  335 docs (FastMCP)      → 30     (cap binds; unchanged)
      -  777 docs (LangChain)    → 30     (cap binds; unchanged)

    Floor 8 = UMAP-recommended minimum for meaningful manifold learning
    (below that, the k-NN graph fragments into disconnected components).
    Divisor 4 = empirical: UMAP recommends `n_neighbors ≤ N/4` to preserve
    local density for density-based downstream clustering (HDBSCAN docs).
    """
    return min(_UMAP_N_NEIGHBORS_CAP, max(8, n_docs // 4))


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
