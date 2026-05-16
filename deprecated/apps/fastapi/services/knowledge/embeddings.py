"""
Knowledge Distiller — Dense Embeddings Service (rotator-based, May 2026)

All embedding calls go through the LiteLLM Router's `kd-embed` group:
NIM `nvidia/llama-nemotron-embed-1b-v2` (2048-dim), single entry. No local
hosting (Xinference removed 2026-05-09 night), no fastembed fallback.

Why this shape:
  - Hosted free-tier (NIM 40 RPM, no monthly cap, commercial OK) covers KD's
    ~14 batched calls per study 100×.
  - Same NVIDIA_API_KEY already in coelhonexus-secret.
  - **Single-entry by design** — embedding rotation across providers breaks
    cosine geometry mid-study (different model = different vector space).
    See memory: project_planner_map_replacement.md regression #5.
  - **No fastembed fallback** — local CPU embedding causes spikes that
    crashed our single-node K8s on bulk operations. Same problem as
    Xinference. Fail fast on rotator outage instead.

R7 disk cache (2026-05-11):
  - Each text input → cache key `f"{KD_EMBED_GROUP}:{sha256(text).hex()}"`.
  - Cache value: the 2048-dim float vector returned by NIM.
  - Cache hits skip the round-trip; misses go to NIM, then are stored.
  - Cache dir `/app/.embed_cache`, size cap 1 GiB (≈65K cached texts at
    2048×4 bytes each + diskcache index). Ephemeral within the pod —
    great for tuning-loop re-runs of the same corpus, lost on pod
    restart. Persistent storage would require a PVC mount.
  - Cache key includes the rotator group name so a future kd-embed model
    swap auto-invalidates (the keys carry the model identity).

Used by:
  - graphs/knowledge/reduce_cluster.py     (REDUCE step, micro-cluster embeddings)
  - graphs/knowledge/hierarchical_synth.py (synth audit — section + hash vecs)
  - graphs/knowledge/preview.py            (preview clustering)
  - graphs/knowledge/classical_map.py      (Planner MAP step replacement)
  - graphs/knowledge/helpers.py            (semantic off-topic noise filter)

Public API:
  embed_texts(texts)       -> (vectors, provider_label)   # async
  embed_texts_sync(texts)  -> (vectors, provider_label)   # sync
  community_detection(embeddings, threshold, min_community_size)   # numpy
  smoke_test()             -> dict                        # /debug
"""
import asyncio
import hashlib
import logging
import math
import os
import time

import diskcache as _dc
import numpy as np

from services.llm_chain import (
    KD_EMBED_GROUP,
    embed_via_router_async,
    embed_via_router_sync,
)


logger = logging.getLogger(__name__)


# Provider label used in tuple returns + log messages. Captures both the
# rotator group and the model name we're routing to (for traceability when
# the Router cools a deployment down to its alternate).
_PROVIDER_LABEL = f"rotator:{KD_EMBED_GROUP}"


# =============================================================================
# R7 (2026-05-11) — on-disk cache for kd-embed vectors
# =============================================================================
# Keyed on `(rotator_group, sha256(text))`. Pinning the group name in the key
# means a future kd-embed model swap auto-invalidates (vectors from a
# different model live in a different geometry — see the project memory note
# on regression #5). 1 GiB cap stores ~65K cached texts; LRU eviction when
# full. Module-level singleton because diskcache is fork-safe (filelock-based
# coordination across Celery prefork children).
_EMBED_CACHE_DIR = os.environ.get("KD_EMBED_CACHE_DIR", "/app/.embed_cache")
_EMBED_CACHE_SIZE_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB

try:
    os.makedirs(_EMBED_CACHE_DIR, exist_ok=True)
    _EMBED_CACHE: _dc.Cache | None = _dc.Cache(
        _EMBED_CACHE_DIR, size_limit=_EMBED_CACHE_SIZE_BYTES,
    )
except Exception as _cache_err:  # pragma: no cover — defensive
    logger.warning(
        f"[embeddings] disk cache init failed "
        f"({type(_cache_err).__name__}: {_cache_err}); "
        f"continuing without cache (every call hits NIM)"
    )
    _EMBED_CACHE = None


def _embed_cache_key(text: str) -> str:
    """Stable cache key. Includes rotator group so kd-embed model swaps
    invalidate transparently."""
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"{KD_EMBED_GROUP}:{digest}"


def _cache_lookup_partition(
    texts: list[str],
) -> tuple[list[list[float] | None], list[int], list[str], int]:
    """
    Split `texts` into cache-hits and -misses preserving order.

    Returns:
        vectors_out: list with cached vectors at hit positions, None at miss
        miss_indices: positions of `None`s in vectors_out
        miss_texts:   texts at miss_indices (input to the rotator)
        hits:         count of cache hits (for logging)
    """
    if _EMBED_CACHE is None:
        return [None] * len(texts), list(range(len(texts))), list(texts), 0
    vectors_out: list[list[float] | None] = [None] * len(texts)
    miss_indices: list[int] = []
    miss_texts: list[str] = []
    hits = 0
    for i, text in enumerate(texts):
        try:
            cached = _EMBED_CACHE.get(_embed_cache_key(text))
        except Exception:
            cached = None  # defensive: corrupt cache entry → treat as miss
        if cached is not None:
            vectors_out[i] = cached
            hits += 1
        else:
            miss_indices.append(i)
            miss_texts.append(text)
    return vectors_out, miss_indices, miss_texts, hits


def _cache_store(texts: list[str], vectors: list[list[float]]) -> None:
    """Best-effort cache store. Failures are logged at debug level only —
    we never block a successful embed call on cache writes."""
    if _EMBED_CACHE is None:
        return
    for text, vec in zip(texts, vectors):
        try:
            _EMBED_CACHE.set(_embed_cache_key(text), vec)
        except Exception as e:  # pragma: no cover — defensive
            logger.debug(f"[embeddings] cache set failed: {type(e).__name__}: {e}")


# =============================================================================
# Public API — embed_texts (sync + async)
# =============================================================================
def embed_texts_sync(texts: list[str]) -> tuple[list[list[float]], str]:
    """
    Synchronous batch embed via the LiteLLM rotator's `kd-embed` group.
    Returns (vectors, provider_label). vectors are 2048-dim float lists,
    one per input, in input order.

    R7 (2026-05-11): each input is looked up in the disk cache first
    (key = `f"{KD_EMBED_GROUP}:{sha256(text).hex()}"`); only misses go
    to NIM. Cache writes are best-effort.

    Raises on full provider outage — caller should let the request fail and
    rely on user-side retry. **Do NOT add a fallback to a different model**
    (different geometry, breaks downstream cosine clustering). See module
    docstring + memory: project_planner_map_replacement.md regression #5.
    """
    if not texts:
        return [], "empty"
    t0 = time.time()
    vectors_out, miss_indices, miss_texts, hits = _cache_lookup_partition(texts)
    if miss_texts:
        miss_vectors = embed_via_router_sync(miss_texts)
        for idx, vec in zip(miss_indices, miss_vectors):
            vectors_out[idx] = vec
        _cache_store(miss_texts, miss_vectors)
    vectors: list[list[float]] = [v for v in vectors_out if v is not None]
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"embed_texts_sync: cache partition invariant violated — "
            f"got {len(vectors)} vectors for {len(texts)} inputs "
            f"({hits} hits, {len(miss_texts)} misses)"
        )
    logger.info(
        f"[embeddings] {KD_EMBED_GROUP} ok "
        f"({len(texts)} items, {len(vectors[0]) if vectors else 0}d, "
        f"in {time.time() - t0:.2f}s, cache: {hits}/{len(texts)} hit)"
    )
    return vectors, _PROVIDER_LABEL


async def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Async equivalent of embed_texts_sync. Same contract, same failure modes."""
    if not texts:
        return [], "empty"
    t0 = time.time()
    vectors_out, miss_indices, miss_texts, hits = _cache_lookup_partition(texts)
    if miss_texts:
        miss_vectors = await embed_via_router_async(miss_texts)
        for idx, vec in zip(miss_indices, miss_vectors):
            vectors_out[idx] = vec
        _cache_store(miss_texts, miss_vectors)
    vectors: list[list[float]] = [v for v in vectors_out if v is not None]
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"embed_texts: cache partition invariant violated — "
            f"got {len(vectors)} vectors for {len(texts)} inputs "
            f"({hits} hits, {len(miss_texts)} misses)"
        )
    logger.info(
        f"[embeddings] {KD_EMBED_GROUP} ok "
        f"({len(texts)} items, {len(vectors[0]) if vectors else 0}d, "
        f"in {time.time() - t0:.2f}s, cache: {hits}/{len(texts)} hit)"
    )
    return vectors, _PROVIDER_LABEL


# =============================================================================
# community_detection — pure-Python greedy O(N²) cosine clustering
# =============================================================================
# Drop-in for sentence_transformers.util.community_detection without the
# torch dependency. Deterministic and fast at our N≤200 scale.
def community_detection(
    embeddings: np.ndarray,
    threshold: float = 0.6,
    min_community_size: int = 2,
) -> list[list[int]]:
    """
    Greedy O(N²) cosine-based community detection.

    Args:
        embeddings: (N, D) array. Will be L2-normalized internally.
        threshold:  cosine similarity required for community membership.
        min_community_size: minimum members for a valid community.

    Returns:
        List of communities (each a sorted list of indices into `embeddings`),
        ordered by size descending. Indices not in any returned community are
        treated as "singletons / unused" by callers.
    """
    arr = np.asarray(embeddings, dtype=np.float32)
    n = len(arr)
    if n == 0:
        return []
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    normalized = arr / np.maximum(norms, 1e-12)
    sim = normalized @ normalized.T  # (N, N)
    candidates: list[list[int]] = []
    for i in range(n):
        members = np.where(sim[i] >= threshold)[0].tolist()
        if len(members) >= min_community_size:
            candidates.append(sorted(members))
    candidates.sort(key=lambda m: (-len(m), m[0] if m else 0))
    used: set[int] = set()
    communities: list[list[int]] = []
    for members in candidates:
        unique = [m for m in members if m not in used]
        if len(unique) >= min_community_size:
            communities.append(sorted(unique))
            used.update(unique)
    return communities


# =============================================================================
# smoke_test — quick sanity check for /debug/embeddings_smoke
# =============================================================================
def smoke_test() -> dict:
    """
    Verify the embeddings stack: round-trip works AND cosine geometry is sane.
    Returns {provider, dim, sim_close, sim_far, margin, ok}. Raises on
    geometry failure (similar pair scores ≤ different pair).
    """
    test_texts = [
        "terragrunt configuration --- Configure terragrunt.hcl with options",
        "configure terragrunt --- Set up terragrunt configuration files",
        "kubernetes deployment --- Deploy applications to a kubernetes cluster",
    ]
    vectors, provider = embed_texts_sync(test_texts)
    if len(vectors) != 3:
        raise RuntimeError(f"smoke: expected 3 vectors, got {len(vectors)}")

    def _cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        ma = math.sqrt(sum(x * x for x in a))
        mb = math.sqrt(sum(x * x for x in b))
        return dot / (ma * mb) if (ma and mb) else 0.0

    sim_close = _cos(vectors[0], vectors[1])
    sim_far = _cos(vectors[0], vectors[2])
    if sim_close <= sim_far:
        raise RuntimeError(
            f"smoke: similar pair ({sim_close:.3f}) "
            f"<= different pair ({sim_far:.3f}) — geometry broken"
        )
    return {
        "provider": provider,
        "dim": len(vectors[0]),
        "sim_close": round(sim_close, 4),
        "sim_far": round(sim_far, 4),
        "margin": round(sim_close - sim_far, 4),
        "ok": True,
    }
