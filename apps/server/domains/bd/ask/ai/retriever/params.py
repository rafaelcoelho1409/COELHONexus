"""Compatibility import path for shared BD RAG retrieval settings."""
from __future__ import annotations
import domains


def __getattr__(name: str):
    for package in (domains.bd.rag.ai.retrieval.params, domains.bd.rag.source.retrieval.params):
        try:
            return getattr(package, name)
        except AttributeError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
