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
import logging
import math
import time

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
# Public API — embed_texts (sync + async)
# =============================================================================
def embed_texts_sync(texts: list[str]) -> tuple[list[list[float]], str]:
    """
    Synchronous batch embed via the LiteLLM rotator's `kd-embed` group.
    Returns (vectors, provider_label). vectors are 2048-dim float lists,
    one per input, in input order.

    Raises on full provider outage — caller should let the request fail and
    rely on user-side retry. **Do NOT add a fallback to a different model**
    (different geometry, breaks downstream cosine clustering). See module
    docstring + memory: project_planner_map_replacement.md regression #5.
    """
    if not texts:
        return [], "empty"
    t0 = time.time()
    vectors = embed_via_router_sync(texts)
    logger.info(
        f"[embeddings] {KD_EMBED_GROUP} ok "
        f"({len(texts)} items, {len(vectors[0]) if vectors else 0}d, "
        f"in {time.time() - t0:.2f}s)"
    )
    return vectors, _PROVIDER_LABEL


async def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Async equivalent of embed_texts_sync. Same contract, same failure modes."""
    if not texts:
        return [], "empty"
    t0 = time.time()
    vectors = await embed_via_router_async(texts)
    logger.info(
        f"[embeddings] {KD_EMBED_GROUP} ok "
        f"({len(texts)} items, {len(vectors[0]) if vectors else 0}d, "
        f"in {time.time() - t0:.2f}s)"
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
