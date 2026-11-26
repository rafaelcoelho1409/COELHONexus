"""Publicly safe errors for BD Agentic RAG run failures."""
from __future__ import annotations


class EmbeddingPreflightError(RuntimeError):
    """The configured embedding endpoint could not be validated before indexing."""
