"""embeddings errors — module exception classes (subclass builtins so
generic `except TimeoutError` / `except RuntimeError` callers keep working)."""
from __future__ import annotations


class EmbeddingError(RuntimeError):
    """Base for embedding-endpoint failures (auth, server 5xx, SDK missing)."""


class EmbeddingTimeoutError(TimeoutError):
    """Probe backstop fired — the call exceeded timeout_s."""
