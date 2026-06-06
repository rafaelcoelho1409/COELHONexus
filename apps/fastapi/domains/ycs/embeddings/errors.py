"""ycs/embeddings — NIM-side exceptions."""
from __future__ import annotations


class EmbeddingError(Exception):
    """Base — anything that surfaces from the NIM embedding call."""


class EmbeddingAPIError(EmbeddingError):
    """Non-retryable upstream error (4xx other than 429, or 5xx after
    `MAX_RETRIES` exhausted)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"NIM embedding API HTTP {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


class EmbeddingEmptyQueryError(EmbeddingError):
    """`embed_query` called with empty / whitespace-only input. NIM
    rejects this; we raise locally so the caller gets a clean signal."""
