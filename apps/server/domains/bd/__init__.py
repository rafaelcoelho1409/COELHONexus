"""bd — Book Distiller bounded context: a book (PDF now, EPUB later) in, a distilled study vault out.

Ingestion is the first stage: `ingestion.source.runtime.dispatch.service.run(book_id, run_id)` extracts the book page by page.
Cross-module references go through the dotted `domains.*` path (docs/CODE-CONVENTIONS.md §8)."""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from . import ask, distill, ingestion

if TYPE_CHECKING:
    from . import query, rag


__all__ = ["ask", "distill", "ingestion", "query", "rag"]


def __getattr__(name: str):
    if name in {"query", "rag"}:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
