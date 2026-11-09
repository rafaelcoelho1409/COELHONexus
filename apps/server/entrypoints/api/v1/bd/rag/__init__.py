"""HTTP routes for book-scoped Agentic RAG indexing."""
from __future__ import annotations

from . import schemas
from .router import router

__all__ = ["router", "schemas"]
