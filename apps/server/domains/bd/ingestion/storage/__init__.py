"""MinIO storage of a book: source PDF, manifest, page markdown, structured slices, figures."""
from __future__ import annotations
from . import domain, keys, params, patterns, service


__all__ = ["domain", "keys", "params", "patterns", "service"]
