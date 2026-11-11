"""bd — Book Distiller bounded context: a book (PDF now, EPUB later) in, a distilled study vault out.

Ingestion is the first stage: `ingestion.runtime.dispatch.service.run(book_id, run_id)` extracts the book page by page.
Cross-module references go through the dotted `domains.*` path (docs/CODE-CONVENTIONS.md §8)."""
from __future__ import annotations
from . import ask, distill, ingestion, rag

__all__ = ["ask", "distill", "ingestion", "rag"]
