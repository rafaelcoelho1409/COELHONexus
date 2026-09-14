"""ycs/embeddings — external-endpoint embedding client + sparse BM25 factory.

Imperative Shell (`docs/CODE-CONVENTIONS.md` §4): I/O, batch pacing,
logging. Pure decisions delegated to `domain.py`.

Implements the LangChain `Embeddings` interface so it slots into
`langchain_qdrant`'s hybrid retriever without adaptation — same contract
as before, just backed by a flexible endpoint instead of a hardcoded NIM
call.

2026-09-13: replaced the direct-to-NIM HTTP client with calls to
`domains.llm.embeddings` (the Settings-page "Embedding" card — COELHO LLM
Rotator by default, any OpenAI-compatible embedding service if pointed
elsewhere). This removes YCS's single hardcoded-provider dependency — the
exact failure mode that silently broke every Qdrant ingestion run for
weeks after NIM retired the previously-hardcoded model on 2026-08-25.
Dimension is learned from the real endpoint response, never hardcoded —
no provider publishes it in a models listing (same finding that shaped
the rotator's own Embedding Curator this session)."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from langchain_core.embeddings import Embeddings
from langchain_qdrant import FastEmbedSparse

from domains.llm.embeddings import embed_probe_async, embed_texts_async

from . import domain
from .errors import EmbeddingAPIError, EmbeddingEmptyQueryError
from .params import (
    BATCH_PAUSE_S,
    BATCH_SIZE,
    PROBE_RETRY_ATTEMPTS,
    PROBE_RETRY_BACKOFF_S,
    SPARSE_MODEL_NAME,
)


logger = logging.getLogger(__name__)

# Dedicated thread pool bridging LangChain's sync `Embeddings` interface to
# the real async call — for callers we don't directly control (e.g.
# langchain_qdrant's own internals may invoke embed_documents/embed_query
# synchronously as part of standard vector-store operations). Our own two
# known call sites (ingestion/service.py, retriever/qdrant_hybrid.py) are
# already async and call the a*-prefixed methods directly, never hitting
# this bridge. A fresh event loop per call (via asyncio.run in a separate
# thread) is safe regardless of whether the CALLING thread already has one
# running — asyncio.run()/run_until_complete() would raise in that case,
# a separate thread never has that conflict.
_bridge_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ycs-embed-bridge")


def _run_async_sync(coro):
    return _bridge_executor.submit(asyncio.run, coro).result()


class ExternalEmbeddings(Embeddings):
    """Embedding client backed by the Settings-page-configured endpoint,
    not a hardcoded provider. `dimensions` starts `None` and is learned
    from the first real response — callers that need it upfront (Qdrant
    collection sizing) should call `probe()` explicitly rather than
    assuming a batch call has already happened."""

    def __init__(self) -> None:
        self.dimensions: int | None = None
        self.last_model: str | None = None
        logger.info("[ycs:embeddings] backed by the configured Embedding endpoint")

    def _record(self, vectors: list[list[float]], model: str | None = None) -> None:
        if vectors and vectors[0]:
            self.dimensions = len(vectors[0])
        if model:
            self.last_model = model

    async def probe(self) -> tuple[int, str]:
        """One tiny real call to learn (dimensions, model) upfront —
        needed at Qdrant collection-create time, before any real batch
        has necessarily run yet.

        2026-09-14: retries with backoff (same shape as
        `aembed_documents` below) — `embed_probe_async` itself stays a
        single-shot 20s primitive, but a cold embedding endpoint
        routinely needs longer than one 20s window to answer its first
        request post-redeploy. Without this, every Qdrant flush on a
        freshly-started worker had exactly one 20s shot at a possibly-
        still-warming-up endpoint, with no time given to recover
        between attempts."""
        last_err: Exception | None = None
        for attempt in range(PROBE_RETRY_ATTEMPTS):
            try:
                vector, meta = await embed_probe_async()
                self._record([vector] if vector else [], meta.get("deployment"))
                return self.dimensions or 0, self.last_model or ""
            except Exception as e:
                last_err = e
                logger.warning(
                    f"[ycs:embeddings] probe attempt {attempt + 1}/"
                    f"{PROBE_RETRY_ATTEMPTS} failed "
                    f"({type(e).__name__}: {e})"
                )
                if attempt + 1 < PROBE_RETRY_ATTEMPTS:
                    await asyncio.sleep(PROBE_RETRY_BACKOFF_S * (attempt + 1))
        assert last_err is not None
        raise last_err

    # --- async (preferred) — our own call sites use these directly ---

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batched with the same pacing the old NIM-direct client used —
        conservative default, not a hard requirement now that the
        endpoint owns its own provider-level rate-limit handling. See
        params.py for the real-scale-test note."""
        if domain.is_empty_input(texts):
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            try:
                vectors = await embed_texts_async(batch)
            except Exception as e:
                raise EmbeddingAPIError(0, f"{type(e).__name__}: {e}") from e
            self._record(vectors)
            out.extend(vectors)
            if i + BATCH_SIZE < len(texts):
                await asyncio.sleep(BATCH_PAUSE_S)
        return out

    async def aembed_query(self, text: str) -> list[float]:
        """Single-shot — no batching, no pacing."""
        if not text or not text.strip():
            raise EmbeddingEmptyQueryError("query text was empty")
        vectors = await self.aembed_documents([text])
        if not vectors:
            raise EmbeddingAPIError(0, "embedding endpoint returned no result for query")
        return vectors[0]

    # --- sync (LangChain ABC compliance, for callers we don't control) ---

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return _run_async_sync(self.aembed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        return _run_async_sync(self.aembed_query(text))


_dense: Optional[ExternalEmbeddings] = None


def create_dense_embeddings() -> ExternalEmbeddings:
    """Lazy singleton; downstream consumers re-use the same instance."""
    global _dense
    if _dense is None:
        _dense = ExternalEmbeddings()
    return _dense


async def get_embedding_info() -> tuple[int, str]:
    """(dimensions, model) for the currently-configured embedding
    endpoint — needed at Qdrant collection-create/schema-check time.
    Replaces the old static `MODEL_DIMENSIONS` lookup: the model (and
    therefore the dimension) is chosen dynamically by whatever endpoint
    is configured, so this makes one real probe call rather than trusting
    a hardcoded table that goes stale the moment the endpoint's pick
    changes."""
    dense = create_dense_embeddings()
    if dense.dimensions is not None and dense.last_model:
        return dense.dimensions, dense.last_model
    return await dense.probe()


_sparse: Optional[FastEmbedSparse] = None


def create_sparse_embeddings() -> FastEmbedSparse:
    """BM25 sparse — local, deterministic, tiny CPU cost. Unrelated to the
    dense embedding endpoint above.

    Lazy singleton (2026-06-10): FastEmbedSparse init loads (and on a
    fresh pod, downloads) the fastembed model — ~1 s warm, tens of
    seconds cold. One instance per worker process is enough; it's
    stateless across calls."""
    global _sparse
    if _sparse is None:
        _sparse = FastEmbedSparse(model_name = SPARSE_MODEL_NAME)
    return _sparse
