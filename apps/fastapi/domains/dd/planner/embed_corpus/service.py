"""embed_corpus helpers — hashing, serialization, chunking, OTel."""
from __future__ import annotations

import hashlib
import io
import unicodedata

import numpy as np

from domains.llm.rotator.chain import DD_EMBED_MODEL_NAME

from .constants import _CACHE_VERSION, _CHUNK_CHARS, _EMBED_PREFIX


def normalize_content(text: str) -> str:
    """Phase B (2026-05-23) — canonical text normalization applied before any
    content-hashing. Fixes the "COLD twice on identical manifest" pattern
    observed across FastMCP+LangChain runs (research-confirmed: content-
    normalization drift is a known cache-miss cause; CRLF vs LF was the
    likely culprit). Stable normalization rules:

      1. NFC Unicode normalization (canonical composed form)
      2. Line endings: CRLF → LF
      3. Strip leading/trailing whitespace
    """
    return (
        unicodedata.normalize("NFC", text or "")
        .replace("\r\n", "\n")
        .strip()
    )


def _manifest_hash(keys: list[str], total_bytes: int) -> str:
    """Stable cache key — same corpus (same keys + same byte count + same
    model + same cache version) → same hash → same MinIO blob. Re-runs
    after a re-ingestion that changes the corpus produce a different
    hash and re-embed. Model swaps also invalidate cleanly because the
    DD_EMBED_MODEL_NAME is part of the digest.

    Phase B (2026-05-23): added explicit dim + input_type fields. The dim
    matters because the new 8B embedder is 4096-D vs the legacy 1B's 2048-D
    — if both versions ever co-exist via env override, their blobs must not
    collide. The cache-version bump in constants is also a safety belt.
    """
    h = hashlib.sha256()
    h.update(f"model={DD_EMBED_MODEL_NAME}|".encode("utf-8"))
    h.update(f"version={_CACHE_VERSION}|".encode("utf-8"))
    h.update(f"input_type=passage|".encode("utf-8"))
    h.update(f"bytes={total_bytes}|".encode("utf-8"))
    for k in sorted(keys):
        h.update(k.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def _blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_EMBED_PREFIX}/{slug}/embeddings/{manifest_hash}.npz"


def _pack_npz(keys: list[str], vectors: np.ndarray) -> bytes:
    """Serialize {keys, vectors} to a compressed .npz byte blob.
    vectors must already be the float32 2-D matrix; keys go in as
    object-dtype 1-D array."""
    arr_keys = np.array(keys, dtype=object)
    buf = io.BytesIO()
    np.savez_compressed(buf, keys=arr_keys, vectors=vectors)
    return buf.getvalue()


def load_embeddings(blob_bytes: bytes) -> tuple[list[str], np.ndarray]:
    """Inverse of _pack_npz. Returned for use by downstream nodes
    (off_topic, cluster) so they share one decoder. Vectors are already
    L2-normalized (so cosine = dot product)."""
    buf = io.BytesIO(blob_bytes)
    with np.load(buf, allow_pickle=True) as data:
        keys = [str(k) for k in data["keys"].tolist()]
        vectors = np.asarray(data["vectors"], dtype=np.float32)
    return keys, vectors


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize. Zero-norm rows return zero (would have
    produced NaN otherwise — happens on empty embeddings)."""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return (mat / norms).astype(np.float32)


def _chunk_doc(body: str) -> list[str]:
    """Split a single document body into <=_CHUNK_CHARS pieces by simple
    character offset. We don't try to chunk on sentence boundaries — the
    embedding model handles partial-sentence inputs fine, and naive
    fixed-size chunking beat semantic chunking 69% vs 54% on the Vecta
    2026 RAG benchmark."""
    if not body:
        return [" "]
    if len(body) <= _CHUNK_CHARS:
        return [body]
    return [body[i:i + _CHUNK_CHARS] for i in range(0, len(body), _CHUNK_CHARS)]


def _attach_otel_attrs(stats: dict) -> None:
    """Decorate the active @traced span with embed.* attributes so
    LangFuse + Alloy see the metrics under the embed_corpus span."""
    try:
        from opentelemetry import trace as _otel_trace
        span = _otel_trace.get_current_span()
        for k, v in stats.items():
            if v is None:
                continue
            span.set_attribute(f"embed.{k}", v)
    except Exception:
        pass
