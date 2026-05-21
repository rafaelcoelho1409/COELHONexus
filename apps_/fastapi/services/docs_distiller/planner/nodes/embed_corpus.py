"""Substep 2 — embed_corpus: one-shot embedding pass over the entire corpus.

Reads page bodies for every key in `raw_files`, embeds them through the
NIM rotator (`dd-embed` group → `nvidia/llama-embed-nemotron-8b`), and
writes the {key → unit-norm vector} matrix as a compact .npz blob to
MinIO under:

    planner/{slug}/embeddings/{manifest_hash}.npz

Downstream substeps (off_topic, cluster) read from this blob instead of
re-embedding — the 3× NIM-cost regression we'd otherwise pay if every
node ran its own embedding loop.

Cache key (`manifest_hash`) includes (sorted_keys + total_bytes + model
name + version tag). Re-runs of the same corpus on the same model hit
the cache; model swaps invalidate cleanly.

Long-doc handling (2026-05-17 upgrade): docs longer than _CHUNK_CHARS
are split into chunks, each embedded as `input_type="passage"`, then
L2-normalized and mean-pooled to ONE vector per document — captures
~70% of late-chunking benefit at $0 vs naive truncation (Vecta 2026
benchmark + research). Stored vectors are pre-normalized so downstream
cosine becomes a single matrix multiplication.

State writes:
  embeddings_ref — MinIO key of the .npz blob (or "" if no input)
  embed_stats    — observability dict: files / dim / cache_hit / wall_ms
                   / store_path / chunked_count / model
"""
from __future__ import annotations

import hashlib
import io
import logging
import time

import numpy as np

from services.docs_distiller.ingestion.storage_minio import get_storage
from services.llm.chain import DD_EMBED_MODEL_NAME, embed_via_router_async

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState


logger = logging.getLogger(__name__)


# Threshold for the chunk-and-pool path. Below this, embed the whole doc
# in one shot; above this, chunk to bound NIM batch payload. 8 KB is the
# v1-validated truncation cap; using it as a chunk-size keeps per-chunk
# behavior identical to the previous truncate-then-embed path while
# preserving signal from the rest of the document.
_CHUNK_CHARS = 8000
_EMBED_PREFIX = "planner"
# Bumped every time the embedding semantics change (model swap, chunk
# strategy, normalization) so stored .npz blobs invalidate cleanly.
_CACHE_VERSION = "v2-2026-05-17"


def _manifest_hash(keys: list[str], total_bytes: int) -> str:
    """Stable cache key — same corpus (same keys + same byte count + same
    model + same cache version) → same hash → same MinIO blob. Re-runs
    after a re-ingestion that changes the corpus produce a different
    hash and re-embed. Model swaps also invalidate cleanly because the
    DD_EMBED_MODEL_NAME is part of the digest."""
    h = hashlib.sha256()
    h.update(f"model={DD_EMBED_MODEL_NAME}|".encode("utf-8"))
    h.update(f"version={_CACHE_VERSION}|".encode("utf-8"))
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


@traced("embed_corpus")
async def embed_corpus(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    raw_files = state.get("raw_files") or []
    corpus_stats = state.get("corpus_stats") or {}
    if not slug or not raw_files:
        return {
            "embeddings_ref": "",
            "embed_stats": {
                "files": 0, "dim": 0, "cache_hit": False,
                "wall_ms": 0, "store_path": "", "skipped": "no input",
            },
        }

    total_bytes = int(corpus_stats.get("total_bytes") or 0)
    mh = _manifest_hash(raw_files, total_bytes)
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    t0 = time.monotonic()
    await emit_progress(
        thread_id, "embed_corpus", "start",
        files=len(raw_files), model=DD_EMBED_MODEL_NAME,
    )
    # ── Cache fast-path ──────────────────────────────────────────────
    if await minio.exists(blob_key):
        try:
            blob = await minio.read_bytes(blob_key)
            cached_keys, cached_vecs = load_embeddings(blob)
            dim = int(cached_vecs.shape[1]) if cached_vecs.ndim == 2 else 0
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "files":         len(cached_keys),
                "dim":           dim,
                "cache_hit":     True,
                "wall_ms":       elapsed,
                "store_path":    blob_key,
                "manifest_hash": mh,
                "model":         DD_EMBED_MODEL_NAME,
            }
            _attach_otel_attrs(stats)
            logger.info(
                f"[embed_corpus] {slug}: CACHE HIT — {len(cached_keys)} vectors "
                f"({dim}-D), {elapsed} ms"
            )
            await emit_progress(
                thread_id, "embed_corpus", "done",
                cache_hit=True, files=len(cached_keys), dim=dim,
                wall_ms=elapsed,
            )
            return {"embeddings_ref": blob_key, "embed_stats": stats}
        except Exception as e:
            logger.warning(
                f"[embed_corpus] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); re-embedding"
            )

    # ── Cold path: chunk → embed → L2-norm → mean-pool → re-norm ───
    bodies = await minio.read_many(raw_files)

    # Build the flat embedding input + a per-doc chunk-count map so we
    # can pool chunks back into one vector per doc.
    flat_inputs: list[str] = []
    per_doc_chunk_counts: list[int] = []
    chunked_count = 0
    for body in bodies:
        chunks = _chunk_doc(body or "")
        if len(chunks) > 1:
            chunked_count += 1
        flat_inputs.extend(chunks)
        per_doc_chunk_counts.append(len(chunks))

    await emit_progress(
        thread_id, "embed_corpus", "chunks_prepared",
        chunks_total=len(flat_inputs), docs_chunked=chunked_count,
        docs_total=len(raw_files),
    )

    async def _on_batch(n_done: int, n_total: int, batch_size: int) -> None:
        await emit_progress(
            thread_id, "embed_corpus", "batch",
            chunks_done=n_done, chunks_total=n_total, batch_size=batch_size,
        )

    # Single rotator call (auto-batched inside the helper). passage type
    # — docs being indexed, not query/anchor.
    flat_vectors = await embed_via_router_async(
        flat_inputs, input_type="passage", on_batch=_on_batch,
    )
    if len(flat_vectors) != len(flat_inputs):
        raise RuntimeError(
            f"embed_corpus: rotator returned {len(flat_vectors)} vectors for "
            f"{len(flat_inputs)} chunks (slug={slug})"
        )

    flat_mat = np.asarray(flat_vectors, dtype=np.float32)
    flat_mat = _l2_normalize(flat_mat)   # per-chunk normalize before pooling

    # Mean-pool chunks back into one vector per doc; re-normalize the
    # pooled vector so cosine = dot product downstream.
    pooled = np.zeros((len(raw_files), flat_mat.shape[1]), dtype=np.float32)
    offset = 0
    for i, n_chunks in enumerate(per_doc_chunk_counts):
        chunk_block = flat_mat[offset:offset + n_chunks]
        pooled[i] = chunk_block.mean(axis=0)
        offset += n_chunks
    pooled = _l2_normalize(pooled)

    blob = _pack_npz(raw_files, pooled)
    await minio.write(blob_key, blob, content_type="application/octet-stream")

    dim = int(pooled.shape[1]) if pooled.ndim == 2 else 0
    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "files":           len(raw_files),
        "dim":             dim,
        "cache_hit":       False,
        "wall_ms":         elapsed,
        "store_path":      blob_key,
        "manifest_hash":   mh,
        "model":           DD_EMBED_MODEL_NAME,
        "chunked_count":   chunked_count,   # docs that triggered multi-chunk path
        "chunks_total":    len(flat_inputs),
        "blob_bytes":      len(blob),
    }
    _attach_otel_attrs(stats)
    logger.info(
        f"[embed_corpus] {slug}: COLD — {len(raw_files)} vectors ({dim}-D), "
        f"{elapsed} ms, blob={len(blob) // 1024} KB, "
        f"chunked={chunked_count}/{len(raw_files)} docs ({len(flat_inputs)} total chunks)"
    )
    await emit_progress(
        thread_id, "embed_corpus", "done",
        cache_hit=False, files=len(raw_files), dim=dim,
        wall_ms=elapsed, blob_bytes=len(blob),
        chunked_count=chunked_count, chunks_total=len(flat_inputs),
    )
    return {"embeddings_ref": blob_key, "embed_stats": stats}


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
