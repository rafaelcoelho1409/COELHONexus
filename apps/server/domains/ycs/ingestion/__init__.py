"""YCS Ingestion feature: extraction, transcript processing, and indexing."""
from __future__ import annotations

from typing import TYPE_CHECKING
import importlib

from . import domain, keys, params, service


if TYPE_CHECKING:
    from . import (
        chunker,
        embedding_migration,
        embeddings,
        es_index,
        extract,
        graph_builder,
        neo4j_task,
        pipeline_task,
        qdrant_task,
        transcript,
    )


_LAZY_SUBDOMAINS = (
    "chunker",
    "embedding_migration",
    "embeddings",
    "es_index",
    "extract",
    "graph_builder",
    "neo4j_task",
    "pipeline_task",
    "qdrant_task",
    "transcript",
)


__all__ = [
    "domain",
    "keys",
    "params",
    "service",
    *_LAZY_SUBDOMAINS,
]


def __getattr__(name: str):
    if name in _LAZY_SUBDOMAINS:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
