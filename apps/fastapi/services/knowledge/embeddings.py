"""
Knowledge Distiller — Dense Embeddings Service (NIM primary + local fallback)

Used by the Clio-pattern REDUCE (graphs/knowledge/reduce_cluster.py) to embed
shard-level micro-cluster descriptions. Two providers, selectable via env var.

Provider choice rationale (2026-04-22):
  NIM hosted embedding API uses `nvidia/llama-nemotron-embed-1b-v2` — 2048-dim,
  ~12 MTEB-Clustering points higher than our local bge-base (~58 vs ~46 avg).
  On same-domain tight corpora the quality gap shrinks (MTEB is averaged over
  diverse tasks), but it's still a material upgrade and free-tier NIM
  embedding endpoint is SEPARATE from the chat-completions endpoint that's
  been unstable — meaning an LLM-side outage doesn't take out embeddings.

  Local `fastembed BAAI/bge-base-en-v1.5` (768-dim, ONNX, CPU) stays as the
  reliability fallback — zero external dependency, deterministic weights,
  always works.

Modes (env var `KD_EMBEDDING_MODE`):
  "nim_with_fallback" (default) — try NIM, fall back to local on any error
  "nim"                         — NIM only; raise on failure
  "local"                       — skip NIM entirely, always use fastembed

Dimension note: NIM returns 2048-dim, fastembed returns 768-dim. Since the
REDUCE pipeline passes embeddings through UMAP → 5-dim before clustering,
the downstream k-means doesn't care which path produced them — each run is
internally consistent.

Interface: synchronous + async wrappers. Both return `list[list[float]]`.
"""
import asyncio
import logging
import os
import threading
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================
_MODE = os.environ.get("KD_EMBEDDING_MODE", "nim_with_fallback").strip().lower()

_NIM_URL = "https://integrate.api.nvidia.com/v1"
_NIM_KEY = os.environ.get("NVIDIA_API_KEY", "")
_NIM_MODEL = os.environ.get(
    "KD_EMBEDDING_MODEL_NIM",
    "nvidia/llama-nemotron-embed-1b-v2",
)

_LOCAL_MODEL = os.environ.get(
    "KD_EMBEDDING_MODEL_LOCAL",
    "BAAI/bge-base-en-v1.5",
)

# NIM free tier: 40 RPM per model. 50 items per batch is the documented safe
# cap; pacing 2s between batches keeps us at ~1500 items/min.
_NIM_BATCH_SIZE = 50
_NIM_BATCH_PAUSE_S = 2.0
# Retry budget for transient HTTP 5xx / network errors on the NIM side.
_NIM_MAX_RETRIES = 4
# HTTP timeout per request; NIM embedding calls typically complete in <2s.
_NIM_TIMEOUT_S = 60.0


# =============================================================================
# Local fastembed singleton
# =============================================================================
_local_model_lock = threading.Lock()
_local_model_instance = None  # type: Optional["TextEmbedding"]  # noqa: F821


def _get_local_model():
    """Lazy-load the fastembed ONNX model. Thread-safe, one-time cold start."""
    global _local_model_instance
    if _local_model_instance is not None:
        return _local_model_instance
    with _local_model_lock:
        if _local_model_instance is not None:
            return _local_model_instance
        from fastembed import TextEmbedding
        logger.info(f"[embeddings] loading fastembed model {_LOCAL_MODEL!r} (one-time)")
        _local_model_instance = TextEmbedding(model_name=_LOCAL_MODEL)
        logger.info(f"[embeddings] fastembed {_LOCAL_MODEL!r} ready")
        return _local_model_instance


def _embed_local_sync(texts: list[str]) -> list[list[float]]:
    """Sync batch-embed via fastembed ONNX. Returns N vectors in input order."""
    if not texts:
        return []
    model = _get_local_model()
    vectors = list(model.embed(texts))
    return [v.tolist() for v in vectors]


# =============================================================================
# NIM embedding HTTP client
# =============================================================================
def _embed_nim_sync(texts: list[str]) -> list[list[float]]:
    """
    Sync batch-embed via NVIDIA NIM hosted embedding API.

    Batches of `_NIM_BATCH_SIZE` with `_NIM_BATCH_PAUSE_S` between batches to
    stay under 40 RPM. Retries transient 5xx / network errors with exponential
    backoff. Raises on persistent failure (caller can decide whether to fall
    back to local).
    """
    if not texts:
        return []
    if not _NIM_KEY:
        raise RuntimeError("NVIDIA_API_KEY not configured")

    # Empty / whitespace-only entries cause NIM to 400 the whole batch.
    # Replace with a single space — the vector will be near-meaningless but
    # batch integrity is preserved and indices line up.
    clean_texts = [t if (t and t.strip()) else " " for t in texts]

    out: list[list[float]] = []
    with httpx.Client(timeout=_NIM_TIMEOUT_S) as client:
        for batch_start in range(0, len(clean_texts), _NIM_BATCH_SIZE):
            batch = clean_texts[batch_start:batch_start + _NIM_BATCH_SIZE]
            last_err: Optional[Exception] = None
            for attempt in range(_NIM_MAX_RETRIES + 1):
                try:
                    response = client.post(
                        f"{_NIM_URL}/embeddings",
                        headers={
                            "Authorization": f"Bearer {_NIM_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": _NIM_MODEL,
                            "input": batch,
                            "input_type": "passage",
                        },
                    )
                    if response.status_code == 200:
                        data = response.json()
                        out.extend(item["embedding"] for item in data["data"])
                        break
                    # 5xx / 429: transient; retry with backoff
                    if response.status_code == 429 or response.status_code >= 500:
                        last_err = RuntimeError(
                            f"NIM HTTP {response.status_code}: "
                            f"{response.text[:120]}"
                        )
                        if attempt < _NIM_MAX_RETRIES:
                            wait = 2 ** (attempt + 1)
                            logger.info(
                                f"[embeddings] NIM batch "
                                f"[{batch_start}:{batch_start + len(batch)}) "
                                f"HTTP {response.status_code}, "
                                f"retry {attempt + 1}/{_NIM_MAX_RETRIES} in {wait}s"
                            )
                            time.sleep(wait)
                            continue
                        raise last_err
                    # 4xx other: deterministic, don't retry
                    raise RuntimeError(
                        f"NIM embedding error {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                except httpx.HTTPError as e:
                    last_err = e
                    if attempt < _NIM_MAX_RETRIES:
                        wait = 2 ** (attempt + 1)
                        logger.warning(
                            f"[embeddings] NIM network error "
                            f"({type(e).__name__}: {e}); "
                            f"retry {attempt + 1}/{_NIM_MAX_RETRIES} in {wait}s"
                        )
                        time.sleep(wait)
                        continue
                    raise
            # Pace between batches to stay under free-tier RPM
            if batch_start + _NIM_BATCH_SIZE < len(clean_texts):
                time.sleep(_NIM_BATCH_PAUSE_S)

    if len(out) != len(texts):
        raise RuntimeError(
            f"NIM returned {len(out)} embeddings for {len(texts)} inputs"
        )
    return out


# =============================================================================
# Public API — sync + async
# =============================================================================
def embed_texts_sync(
    texts: list[str],
    mode: Optional[str] = None,
) -> tuple[list[list[float]], str]:
    """
    Synchronous batch embed. Returns (vectors, provider_label).

    mode overrides the `KD_EMBEDDING_MODE` env var:
      "nim_with_fallback" — try NIM, fall back to local on any error
      "nim"               — NIM only, raise on failure
      "local"             — local fastembed only

    provider_label is "nim:<model>" or "local:<model>" so callers can log which
    backend actually served the request on fallback cases.
    """
    effective = (mode or _MODE).lower()
    if not texts:
        return [], "empty"

    if effective == "local":
        return _embed_local_sync(texts), f"local:{_LOCAL_MODEL}"

    if effective == "nim":
        return _embed_nim_sync(texts), f"nim:{_NIM_MODEL}"

    # Default: NIM with local fallback
    try:
        t0 = time.time()
        vectors = _embed_nim_sync(texts)
        logger.info(
            f"[embeddings] NIM {_NIM_MODEL} ok "
            f"({len(texts)} items in {time.time() - t0:.2f}s)"
        )
        return vectors, f"nim:{_NIM_MODEL}"
    except Exception as e:
        logger.warning(
            f"[embeddings] NIM {_NIM_MODEL} failed "
            f"({type(e).__name__}: {str(e)[:160]}); falling back to local fastembed"
        )
        t0 = time.time()
        vectors = _embed_local_sync(texts)
        logger.info(
            f"[embeddings] local {_LOCAL_MODEL} ok "
            f"({len(texts)} items in {time.time() - t0:.2f}s)"
        )
        return vectors, f"local:{_LOCAL_MODEL}"


async def embed_texts(
    texts: list[str],
    mode: Optional[str] = None,
) -> tuple[list[list[float]], str]:
    """
    Async wrapper — runs the sync embedder in a worker thread so the event
    loop stays responsive. Use this from async contexts (the default).
    """
    return await asyncio.to_thread(embed_texts_sync, texts, mode)
