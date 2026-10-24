"""ycs/embeddings — exceptions surfaced from the configured embedding endpoint."""
from __future__ import annotations


class EmbeddingError(Exception):
    """Base — anything that surfaces from the embedding endpoint call."""


class EmbeddingAPIError(EmbeddingError):
    """Upstream error from the configured embedding endpoint — no key
    configured, endpoint unreachable, or the endpoint itself returned an
    error. `status_code=0` for non-HTTP failures (e.g. missing key)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Embedding endpoint error (HTTP {status_code}): {body[:200]}")
        self.status_code = status_code
        self.body = body


class EmbeddingEmptyQueryError(EmbeddingError):
    """`embed_query` called with empty / whitespace-only input. We raise
    locally so the caller gets a clean signal instead of a confusing
    upstream error."""
