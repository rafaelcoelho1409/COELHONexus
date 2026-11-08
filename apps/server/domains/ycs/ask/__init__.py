"""YCS Ask and retrieval workflows, with shared telemetry helpers.

The feature package exposes its subdomains lazily so importing one YCS
feature does not load unrelated integration stacks. ``observability`` is
used by Ask, Query, and Ingestion call sites.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import importlib


if TYPE_CHECKING:
    from . import (
        cache,
        conversation,
        grader,
        observability,
        rag,
        reranker,
        retriever,
    )


__all__ = [
    "cache",
    "conversation",
    "grader",
    "observability",
    "rag",
    "reranker",
    "retriever",
]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
