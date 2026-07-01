"""corpus_normalize — idempotent ingestion-time markdown cleanup (no I/O)."""
from .domain import normalize_doc
from .schemas import NormalizedDoc, NormalizeStats


__all__ = ["NormalizedDoc", "NormalizeStats", "normalize_doc"]
