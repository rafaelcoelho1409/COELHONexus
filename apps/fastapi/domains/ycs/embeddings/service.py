"""ycs/embeddings — NIM embedding client + sparse BM25 factory.

Imperative Shell (`docs/CODE-CONVENTIONS.md` §4): HTTP I/O, retry loop,
batch pacing, logging. Pure decisions delegated to `domain.py`.

Implements the LangChain `Embeddings` interface so it slots into
`langchain_qdrant`'s hybrid retriever without adaptation. Direct port
of deprecated `services/youtube/embeddings.py:L59-194`."""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from langchain_core.embeddings import Embeddings
from langchain_qdrant import FastEmbedSparse

from . import domain
from .errors import EmbeddingAPIError, EmbeddingEmptyQueryError
from .params import (
    BATCH_PAUSE_S,
    BATCH_SIZE,
    EMBEDDING_MODEL,
    HTTP_TIMEOUT_S,
    MAX_RETRIES,
    MODEL_DIMENSIONS,
    NIM_KEY,
    NIM_URL,
    SPARSE_MODEL_NAME,
)


logger = logging.getLogger(__name__)


class NVIDIAEmbeddings(Embeddings):
    """NIM embedding API client with retry + rate-limit pacing.

    Sync httpx by design — deprecated chose sync because (a) the
    Celery worker context already wraps everything in `asyncio.run`,
    (b) `time.sleep` between batches inside the gather-bound coroutine
    is a noop-blocker that helps NIM's rate-limit budget, (c) LangChain's
    `Embeddings` interface is sync.
    """

    def __init__(self, model: str = EMBEDDING_MODEL) -> None:
        self.model = model
        self.dimensions = MODEL_DIMENSIONS.get(model, 2048)
        self._client = httpx.Client(timeout = HTTP_TIMEOUT_S)
        logger.info(
            f"[ycs:embeddings] {model} ({self.dimensions}d) via NIM"
        )

    # -------- private API call (retry + backoff) -----------------------

    def _call_api(
        self, texts: list[str], input_type: str = "passage",
    ) -> list[list[float]]:
        if domain.is_empty_input(texts):
            return []
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.post(
                    f"{NIM_URL}/embeddings",
                    headers = {
                        "Authorization": f"Bearer {NIM_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json = {
                        "model":      self.model,
                        "input":      texts,
                        "input_type": input_type,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return [item["embedding"] for item in data["data"]]
                if domain.is_transient_status(response.status_code):
                    if attempt < MAX_RETRIES:
                        wait = domain.backoff_delay_s(attempt)
                        logger.info(
                            f"[ycs:embeddings] HTTP {response.status_code}, "
                            f"retry {attempt + 1}/{MAX_RETRIES} in {wait}s"
                        )
                        time.sleep(wait)
                        continue
                    raise EmbeddingAPIError(response.status_code, response.text)
                # 4xx other than 429: deterministic, no retry.
                raise EmbeddingAPIError(response.status_code, response.text)
            except EmbeddingAPIError:
                raise
            except Exception as e:
                if attempt < MAX_RETRIES:
                    wait = domain.backoff_delay_s(attempt)
                    logger.warning(
                        f"[ycs:embeddings] network error: {e}, "
                        f"retry {attempt + 1}/{MAX_RETRIES} in {wait}s"
                    )
                    time.sleep(wait)
                    continue
                raise
        # Defensive — loop above either returns or raises.
        return []

    # -------- LangChain Embeddings interface ---------------------------

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batched embedding with rate-limit-aware pacing.

        Deprecated math: 50 texts × 30 batches/min ≈ 1500 texts/min,
        comfortably under the 40 RPM NIM ceiling on the embedding model.
        For a 1800-chunk ingest: ~36 batches × 2s pause ≈ 72s pacing +
        API time = ~3-5 min total."""
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            out.extend(self._call_api(batch, input_type = "passage"))
            if i + BATCH_SIZE < len(texts):
                time.sleep(BATCH_PAUSE_S)
        return out

    def embed_query(self, text: str) -> list[float]:
        """Single-shot — no batching, no pacing."""
        if not text or not text.strip():
            raise EmbeddingEmptyQueryError("query text was empty")
        result = self._call_api([text], input_type = "query")
        if not result:
            raise EmbeddingAPIError(0, "NIM returned no result for query")
        return result[0]


# ---------- factories (deprecated re-exports) ---------------------------

_dense: Optional[NVIDIAEmbeddings] = None


def create_dense_embeddings() -> NVIDIAEmbeddings:
    """Lazy singleton; downstream consumers re-use the same httpx client."""
    global _dense
    if _dense is None:
        _dense = NVIDIAEmbeddings(model = EMBEDDING_MODEL)
    return _dense


def get_embedding_dimensions() -> int:
    """Vector size for the configured model — needed at Qdrant
    collection-create time."""
    return MODEL_DIMENSIONS.get(EMBEDDING_MODEL, 2048)


def create_sparse_embeddings() -> FastEmbedSparse:
    """BM25 sparse — local, deterministic, tiny CPU cost. Mirror of
    deprecated `services/youtube/embeddings.py:L189-194`."""
    return FastEmbedSparse(model_name = SPARSE_MODEL_NAME)
