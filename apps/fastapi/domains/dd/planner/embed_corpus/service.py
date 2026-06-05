"""embed_corpus I/O shell — HF tokenizer + chunking + embed_corpus_run.

Tokenizer is a process-singleton from the `tokenizers` Rust wheel (~3 MB,
deterministic BPE lookup, NO model weights / NO inference — preserves the
no-local-inference architecture rule)."""
from __future__ import annotations

import logging
import time

import numpy as np

from domains.llm.rotator.chain import (
    DD_EMBED_MODEL_NAME,
    embed_via_router_async,
)

from ...ingestion.storage import get_storage
from ..observability import attach_span_attrs
from ..progress import emit_progress
from ..state import PlannerState

from .domain import (
    l2_normalize,
    load_embeddings,
    manifest_hash,
    normalize_content,
    pack_npz,
)
from .keys import blob_key
from .params import CHUNK_CHARS_FALLBACK, TOKEN_TARGET


logger = logging.getLogger(__name__)


_TOKENIZER = None


def get_tokenizer():
    """Lazy process-singleton. None on load failure → caller falls back
    to char-based chunking. Uses the standalone `tokenizers` wheel (not
    transformers) for exact NIM-server-side token-count parity."""
    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER
    try:
        from tokenizers import Tokenizer   # deferred — keeps app boot fast
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


def _char_chunks(body: str) -> list[str]:
    if len(body) <= CHUNK_CHARS_FALLBACK:
        return [body]
    return [
        body[i:i + CHUNK_CHARS_FALLBACK]
        for i in range(0, len(body), CHUNK_CHARS_FALLBACK)
    ]


def chunk_doc(body: str) -> list[str]:
    """Split body into ≤TOKEN_TARGET-token chunks via exact-tokenizer
    encode → slice → decode. Char-fallback (CHUNK_CHARS_FALLBACK, ≤8192
    tokens at 1.0 char/token worst case) on tokenizer-load failure.

    No sentence boundaries — naive fixed-size beat semantic 69%→54% on
    the Vecta 2026 RAG benchmark."""
    if not body:
        return [" "]
    tok = get_tokenizer()
    if tok is None:
        return _char_chunks(body)
    try:
        ids = tok.encode(body, add_special_tokens = False).ids
    except Exception as e:
        logger.warning(
            f"[embed_corpus] tokenizer.encode failed: "
            f"{type(e).__name__}: {e} — using char-based fallback"
        )
        return _char_chunks(body)
    if len(ids) <= TOKEN_TARGET:
        return [body]
    chunks: list[str] = []
    for i in range(0, len(ids), TOKEN_TARGET):
        sliced = ids[i:i + TOKEN_TARGET]
        try:
            chunks.append(tok.decode(sliced, skip_special_tokens = True))
        except Exception:
            # Per-chunk decode failure — rest of doc still contributes via mean-pool.
            continue
    return chunks or [body[:CHUNK_CHARS_FALLBACK]]


async def embed_corpus_run(state: PlannerState) -> dict:
    """One-shot embedding pass over the corpus. Caches under
    `planner/{slug}/embeddings/{manifest_hash}.npz`; cold path chunks,
    embeds each chunk as a passage, L2-normalizes, mean-pools per doc."""
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
    mh = manifest_hash(raw_files, total_bytes)
    blob_path = blob_key(slug, mh)
    minio = get_storage()

    t0 = time.monotonic()
    await emit_progress(
        thread_id, "embed_corpus", "start",
        files = len(raw_files),
        model = DD_EMBED_MODEL_NAME,
    )
    if await minio.exists(blob_path):
        try:
            blob = await minio.read_bytes(blob_path)
            cached_keys, cached_vecs = load_embeddings(blob)
            dim = int(cached_vecs.shape[1]) if cached_vecs.ndim == 2 else 0
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "files":         len(cached_keys),
                "dim":           dim,
                "cache_hit":     True,
                "wall_ms":       elapsed,
                "store_path":    blob_path,
                "manifest_hash": mh,
                "model":         DD_EMBED_MODEL_NAME,
            }
            attach_span_attrs("embed", stats)
            logger.info(
                f"[embed_corpus] {slug}: CACHE HIT — {len(cached_keys)} "
                f"vectors ({dim}-D), {elapsed} ms"
            )
            await emit_progress(
                thread_id, "embed_corpus", "done",
                cache_hit = True,
                files = len(cached_keys),
                dim = dim,
                wall_ms = elapsed,
            )
            return {"embeddings_ref": blob_path, "embed_stats": stats}
        except Exception as e:
            logger.warning(
                f"[embed_corpus] {slug}: cached blob {blob_path!r} "
                f"unreadable ({type(e).__name__}: {e}); re-embedding"
            )

    # Cold path: chunk → embed → L2-norm → mean-pool → re-norm.
    bodies = await minio.read_many(raw_files)

    # normalize_content before chunking — whitespace drift would otherwise
    # embed the same content differently across runs (COLD-twice cache miss).
    flat_inputs: list[str] = []
    per_doc_chunk_counts: list[int] = []
    chunked_count = 0
    for body in bodies:
        chunks = chunk_doc(normalize_content(body or ""))
        if len(chunks) > 1:
            chunked_count += 1
        flat_inputs.extend(chunks)
        per_doc_chunk_counts.append(len(chunks))

    await emit_progress(
        thread_id, "embed_corpus", "chunks_prepared",
        chunks_total = len(flat_inputs),
        docs_chunked = chunked_count,
        docs_total = len(raw_files),
    )

    async def _on_batch(n_done: int, n_total: int, batch_size: int) -> None:
        await emit_progress(
            thread_id, "embed_corpus", "batch",
            chunks_done = n_done,
            chunks_total = n_total,
            batch_size = batch_size,
        )

    # Single rotator call (auto-batched). passage = indexed docs, not query.
    flat_vectors = await embed_via_router_async(
        flat_inputs, input_type = "passage", on_batch = _on_batch,
    )
    if len(flat_vectors) != len(flat_inputs):
        raise RuntimeError(
            f"embed_corpus: rotator returned {len(flat_vectors)} vectors "
            f"for {len(flat_inputs)} chunks (slug={slug})"
        )

    flat_mat = np.asarray(flat_vectors, dtype = np.float32)
    flat_mat = l2_normalize(flat_mat)

    # Mean-pool chunks → one vector / doc, re-norm so cosine = dot product.
    pooled = np.zeros((len(raw_files), flat_mat.shape[1]), dtype = np.float32)
    offset = 0
    for i, n_chunks in enumerate(per_doc_chunk_counts):
        chunk_block = flat_mat[offset:offset + n_chunks]
        pooled[i] = chunk_block.mean(axis = 0)
        offset += n_chunks
    pooled = l2_normalize(pooled)

    blob = pack_npz(raw_files, pooled)
    await minio.write(
        blob_path, blob, content_type = "application/octet-stream",
    )

    dim = int(pooled.shape[1]) if pooled.ndim == 2 else 0
    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "files":           len(raw_files),
        "dim":             dim,
        "cache_hit":       False,
        "wall_ms":         elapsed,
        "store_path":      blob_path,
        "manifest_hash":   mh,
        "model":           DD_EMBED_MODEL_NAME,
        "chunked_count":   chunked_count,
        "chunks_total":    len(flat_inputs),
        "blob_bytes":      len(blob),
    }
    attach_span_attrs("embed", stats)
    logger.info(
        f"[embed_corpus] {slug}: COLD — {len(raw_files)} vectors "
        f"({dim}-D), {elapsed} ms, blob={len(blob) // 1024} KB, "
        f"chunked={chunked_count}/{len(raw_files)} docs "
        f"({len(flat_inputs)} total chunks)"
    )
    await emit_progress(
        thread_id, "embed_corpus", "done",
        cache_hit = False,
        files = len(raw_files),
        dim = dim,
        wall_ms = elapsed,
        blob_bytes = len(blob),
        chunked_count = chunked_count,
        chunks_total = len(flat_inputs),
    )
    return {"embeddings_ref": blob_path, "embed_stats": stats}
