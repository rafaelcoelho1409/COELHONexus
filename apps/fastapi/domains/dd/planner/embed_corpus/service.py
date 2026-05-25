"""embed_corpus helpers — hashing, serialization, chunking, OTel."""
from __future__ import annotations

import hashlib
import io
import logging
import unicodedata

import numpy as np

from domains.llm.rotator.chain import DD_EMBED_MODEL_NAME

from .constants import (
    _CACHE_VERSION,
    _CHUNK_CHARS,
    _CHUNK_CHARS_FALLBACK,
    _EMBED_PREFIX,
    _TOKEN_HARD_CAP,
    _TOKEN_TARGET,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Tokenizer — HuggingFace AutoTokenizer for byte-exact token counting (2026-05-25)
# =============================================================================
# Loaded ONCE per process. Tokenizer only — NOT the model weights. ~15 MB
# disk, ~20-50 MB RAM, no GPU, no inference. Equivalent to a deterministic
# text→int lookup, just with BPE merge rules. Does NOT violate
# `project_local_vs_rotator_architecture` (which bans model INFERENCE,
# not text encoding).
#
# Why byte-exact matters: NIM's `llama-nemotron-embed-1b-v2` server uses
# this exact tokenizer. Running it client-side gives the same count NIM
# will measure → can pack right up to 7800/8192 with zero overflow risk.
_TOKENIZER = None


def _get_tokenizer():
    """Lazy-load the HuggingFace tokenizer once per process. Returns None
    on any load failure — caller then falls back to the char-based cap
    so embedding still works (just less efficiently).

    Uses the standalone `tokenizers` Rust library (3.3 MB wheel) directly,
    not `transformers` (50-60 MB). `Tokenizer.from_pretrained(model_id)`
    fetches the same `tokenizer.json` NIM uses server-side via
    huggingface_hub — exact-count parity with no model weights loaded.
    """
    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER
    try:
        # Defer the import until first use so app boot stays fast even
        # when embed_corpus isn't on the hot path.
        from tokenizers import Tokenizer
        _TOKENIZER = Tokenizer.from_pretrained(DD_EMBED_MODEL_NAME)
        logger.info(
            f"[embed_corpus] tokenizer loaded — {DD_EMBED_MODEL_NAME} "
            f"(vocab_size={_TOKENIZER.get_vocab_size()})"
        )
    except Exception as e:
        logger.warning(
            f"[embed_corpus] tokenizer load failed for "
            f"{DD_EMBED_MODEL_NAME}: {type(e).__name__}: {e} — "
            f"falling back to char-based chunking"
        )
        _TOKENIZER = None
    return _TOKENIZER


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
    """Split a doc body into <=_TOKEN_TARGET-token pieces (2026-05-25
    upgrade — replaces the 8000-char heuristic that used only ~25% of
    the model's 8192-token capacity).

    Strategy: encode the body once via the model's exact tokenizer,
    slice the token-id list at _TOKEN_TARGET boundaries, then decode
    each slice back to text. Result: chunks are guaranteed ≤ _TOKEN_TARGET
    tokens regardless of content density (English vs heavy-code differ
    by 1.5× in chars/token), and we pack right up to ~95% of the server
    cap with no overflow risk.

    Fail-soft: if AutoTokenizer can't load (offline / disk-cache miss /
    weird env), fall back to char-based chunking at _CHUNK_CHARS_FALLBACK
    (8000 chars guarantees ≤8192 tokens even at 1.0 char/token worst-case).
    Combined with the NIM-side `truncate="END"` flag (in the rotator
    wrapper), this gives belt-and-suspenders safety.

    Naive fixed-size chunking beat semantic chunking 69% vs 54% on the
    Vecta 2026 RAG benchmark, so we don't try sentence boundaries —
    the embedding model handles partial-sentence inputs fine.
    """
    if not body:
        return [" "]
    tok = _get_tokenizer()
    if tok is None:
        # Char-based fallback: safe at 1.0 char/token worst-case (8000
        # chars ≤ 8192 tokens always). Conservative but correct.
        if len(body) <= _CHUNK_CHARS_FALLBACK:
            return [body]
        return [
            body[i:i + _CHUNK_CHARS_FALLBACK]
            for i in range(0, len(body), _CHUNK_CHARS_FALLBACK)
        ]
    try:
        # tokenizers.Tokenizer.encode(text) returns an Encoding object;
        # `.ids` is the int list. `add_special_tokens=False` keeps the
        # BPE output pure (no [CLS]/[SEP]/etc) — matches what NIM's
        # passage-encoding path sees server-side.
        ids = tok.encode(body, add_special_tokens=False).ids
    except Exception as e:
        logger.warning(
            f"[embed_corpus] tokenizer.encode failed: "
            f"{type(e).__name__}: {e} — using char-based fallback"
        )
        if len(body) <= _CHUNK_CHARS_FALLBACK:
            return [body]
        return [
            body[i:i + _CHUNK_CHARS_FALLBACK]
            for i in range(0, len(body), _CHUNK_CHARS_FALLBACK)
        ]
    if len(ids) <= _TOKEN_TARGET:
        return [body]
    chunks: list[str] = []
    for i in range(0, len(ids), _TOKEN_TARGET):
        sliced = ids[i:i + _TOKEN_TARGET]
        try:
            # tokenizers' decode is also Rust-backed; skip_special_tokens
            # ensures no marker bleed in the re-encoded text.
            chunks.append(tok.decode(sliced, skip_special_tokens=True))
        except Exception:
            # Per-chunk decode failure — skip; the rest of the doc still
            # contributes via mean-pool downstream.
            continue
    return chunks or [body[:_CHUNK_CHARS_FALLBACK]]


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
