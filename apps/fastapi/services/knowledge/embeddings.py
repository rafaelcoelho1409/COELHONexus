"""
Local Dense Embeddings — ONNX (fastembed) for the Knowledge Distiller REDUCE

Used by the new Clio-pattern REDUCE step (graphs/knowledge/reduce_cluster.py)
to turn ~300 shard-level micro-cluster descriptions into 384-dim vectors
that k-means can group into chapter meta-clusters.

Why local and not NVIDIA NIM:
  - KD's REDUCE runs in the Celery worker — we want zero external-API
    dependency on a call that's already 7-layers-deep into the pipeline.
  - NIM's hosted gateway has been unreliable on long-prompt structured
    output (observed 2026-04-22: 504 Gateway Timeout after 300s on the
    REDUCE call). Pulling embeddings into the same provider would mean a
    single NIM outage takes out BOTH map-reduce tiers.
  - fastembed (ONNX-runtime, no torch) is already in pyproject.toml and
    encodes 300 short strings in sub-second on CPU. No GPU needed.

Model choice — BAAI/bge-small-en-v1.5:
  - 384 dims (fast k-means, low memory)
  - Strong on technical/code-framework descriptions (MTEB English leaderboard)
  - ~66 MB download on first use (cached in the container layer afterward)
  - Fastembed's default; well-tested with ONNX runtime

Interface: synchronous encode + async wrapper that offloads to a thread
so the event loop doesn't block while ONNX runs.
"""
import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# Module-level singleton — fastembed loads the ONNX weights once (~66MB)
# on first call; subsequent encodes reuse the same in-memory runtime.
# Guarded by a lock so two concurrent REDUCE tasks don't double-load.
_model_lock = threading.Lock()
_model_instance = None  # type: Optional["TextEmbedding"]  # noqa: F821


def _get_model(model_name: str = _DEFAULT_MODEL):
    """
    Lazy-load a fastembed TextEmbedding model. Thread-safe.

    First call downloads weights (~66MB for bge-small); ~5-10s cold start
    on a fresh container, zero cost on subsequent calls.
    """
    global _model_instance
    if _model_instance is not None:
        return _model_instance
    with _model_lock:
        if _model_instance is not None:
            return _model_instance
        from fastembed import TextEmbedding
        logger.info(f"[embeddings] loading fastembed model {model_name!r} (one-time cold start)")
        _model_instance = TextEmbedding(model_name = model_name)
        logger.info(f"[embeddings] fastembed {model_name!r} ready")
        return _model_instance


def embed_texts_sync(texts: list[str], model_name: str = _DEFAULT_MODEL) -> list[list[float]]:
    """
    Synchronous batch-embed. Returns N vectors in input order.

    Empty inputs: we pad with zero-vectors so the output length matches the
    input length (k-means requires dense matrix input). fastembed's own
    behavior on empty strings is undefined, so we filter + pad deterministically.
    """
    if not texts:
        return []
    model = _get_model(model_name)
    # fastembed returns a generator of np.ndarray (one vector per text)
    vectors = list(model.embed(texts))
    return [v.tolist() for v in vectors]


async def embed_texts(texts: list[str], model_name: str = _DEFAULT_MODEL) -> list[list[float]]:
    """
    Async wrapper — offloads the ONNX call to a worker thread so the
    event loop stays responsive. Use this from async contexts.
    """
    return await asyncio.to_thread(embed_texts_sync, texts, model_name)
