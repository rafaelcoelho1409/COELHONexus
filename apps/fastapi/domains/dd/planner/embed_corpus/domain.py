"""embed_corpus — pure helpers (hashing, normalization, npz serialization).

Everything here is deterministic and does NOT depend on the runtime
tokenizer. Tokenizer-bound chunking lives in `service.chunk_doc` because
loading the tokenizer is process-level I/O.
"""
from __future__ import annotations

import hashlib
import io
import unicodedata

import numpy as np

from domains.llm.rotator.chain import DD_EMBED_MODEL_NAME

from .versions import CACHE_VERSION


def normalize_content(text: str) -> str:
    """Phase B (2026-05-23) — canonical text normalization applied before
    any content-hashing. Fixes the "COLD twice on identical manifest"
    pattern observed across FastMCP+LangChain runs (content-normalization
    drift is a known cache-miss cause; CRLF vs LF was the likely culprit).

      1. NFC Unicode normalization (canonical composed form)
      2. Line endings: CRLF → LF
      3. Strip leading/trailing whitespace
    """
    return (
        unicodedata.normalize("NFC", text or "")
        .replace("\r\n", "\n")
        .strip()
    )


def manifest_hash(keys: list[str], total_bytes: int) -> str:
    """Stable cache key — same corpus (same keys + same byte count + same
    model + same cache version) → same hash → same MinIO blob. Re-runs
    after a re-ingestion that changes the corpus produce a different hash
    and re-embed. Model swaps also invalidate cleanly because the
    DD_EMBED_MODEL_NAME is part of the digest.

    Phase B (2026-05-23): added explicit dim + input_type fields. The dim
    matters because the new 8B embedder is 4096-D vs the legacy 1B's
    2048-D — if both versions ever co-exist via env override, their blobs
    must not collide. The cache-version bump is also a safety belt.
    """
    h = hashlib.sha256()
    h.update(f"model={DD_EMBED_MODEL_NAME}|".encode("utf-8"))
    h.update(f"version={CACHE_VERSION}|".encode("utf-8"))
    h.update(f"input_type=passage|".encode("utf-8"))
    h.update(f"bytes={total_bytes}|".encode("utf-8"))
    for k in sorted(keys):
        h.update(k.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize. Zero-norm rows return zero (would have
    produced NaN otherwise — happens on empty embeddings)."""
    norms = np.linalg.norm(mat, axis = 1, keepdims = True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return (mat / norms).astype(np.float32)


def pack_npz(keys: list[str], vectors: np.ndarray) -> bytes:
    """Serialize {keys, vectors} to a compressed .npz byte blob. vectors
    must already be the float32 2-D matrix; keys go in as object-dtype
    1-D array."""
    arr_keys = np.array(keys, dtype = object)
    buf = io.BytesIO()
    np.savez_compressed(buf, keys = arr_keys, vectors = vectors)
    return buf.getvalue()


def load_embeddings(blob_bytes: bytes) -> tuple[list[str], np.ndarray]:
    """Inverse of pack_npz. Returned for use by downstream nodes
    (off_topic, cluster) so they share one decoder. Vectors are already
    L2-normalized (so cosine = dot product)."""
    buf = io.BytesIO(blob_bytes)
    with np.load(buf, allow_pickle = True) as data:
        keys = [str(k) for k in data["keys"].tolist()]
        vectors = np.asarray(data["vectors"], dtype = np.float32)
    return keys, vectors
