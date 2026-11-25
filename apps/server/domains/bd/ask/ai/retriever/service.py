"""Compatibility import path for shared BD RAG retrieval orchestration."""
from __future__ import annotations
import domains


def __getattr__(name: str):
    service = domains.bd.rag.ai.retrieval.service
    try:
        return getattr(service, name)
    except AttributeError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
