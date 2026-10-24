"""YouCanSee (YCS) — bounded context. Wave 1-4 ingestion pipeline
(extract/chunker/embeddings/es_index/graph_builder/ingestion) +
Wave 5 Ask (rag/retriever/reranker/grader/query/conversation) +
their Celery task packages, re-exported here for cross-package
refs (docs/CODE-CONVENTIONS.md §8 rollout).

The 20 subpackages below are re-exported LAZILY (PEP 562 module
`__getattr__`, 2026-09-24) — see `infra/__init__.py`'s docstring for
the full rationale/measurements. Same fix, same reason: reaching
`domains.ycs.embeddings` alone used to import all 20 (graph_builder's
LangChain stack, reranker's cross-encoder, everything) because this
file had to run to completion first. Each subpackage now only imports
on first actual access."""
from __future__ import annotations
from typing import TYPE_CHECKING
import importlib


if TYPE_CHECKING:
    from . import (
        cache,
        chunker,
        content,
        conversation,
        embedding_migration,
        embeddings,
        es_index,
        extract,
        grader,
        graph_builder,
        ingestion,
        neo4j_task,
        pipeline_task,
        qdrant_task,
        query,
        rag,
        reranker,
        retriever,
        runtime,
        transcript,
    )


__all__ = [
    "cache",
    "chunker",
    "content",
    "conversation",
    "embedding_migration",
    "embeddings",
    "es_index",
    "extract",
    "grader",
    "graph_builder",
    "ingestion",
    "neo4j_task",
    "pipeline_task",
    "qdrant_task",
    "query",
    "rag",
    "reranker",
    "retriever",
    "runtime",
    "transcript",
]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
