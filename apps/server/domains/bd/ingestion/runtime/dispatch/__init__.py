"""Extraction entry point: runtime.dispatch.service.run(book_id, run_id) — gate, outline, page slices, finalize."""
from __future__ import annotations
from . import domain, service


__all__ = ["domain", "service"]
