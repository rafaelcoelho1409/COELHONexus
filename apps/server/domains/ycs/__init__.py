"""YouCanSee (YCS), grouped by product feature.

* ``source`` owns YouTube discovery and source metadata search.
* ``ingestion`` owns extraction, transcription, indexing, storage, and task coordination.
* ``ask`` owns RAG, retrieval, grading, reranking, and conversation state.
  Its observability helpers are shared with ingestion and query.
* ``query`` owns the direct Elasticsearch, Qdrant, and Neo4j workbench.

Feature packages stay lazily imported so a consumer only loads the
integrations used by its request path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import importlib


if TYPE_CHECKING:
    from . import ask, ingestion, query, source


__all__ = ["source", "ingestion", "ask", "query"]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
