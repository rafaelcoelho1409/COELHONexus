"""corpus_normalize — ingestion-time markdown cleanup.

Strips rendering noise from ingested framework docs BEFORE any downstream
consumer (file viewer, embed_corpus, off_topic, cluster, synth) sees the
bytes. Replaces the deprecated post-hoc 9-pass `_scrub_assembled_markdown`
passes 0-2 by moving the cleanup to INPUT-PREP.

See:
  - docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md (synth step 2)
  - May-2026 SOTA research: MDKEYCHUNKER (arXiv 2603.23533) + Markdown-
    First Semantics (Steakhouse 2026) + MARCUS (2506.07116)

Pure functions, no I/O. Idempotent: normalize(normalize(x)) == normalize(x).
"""
from .domain import normalize_doc
from .schemas import NormalizedDoc, NormalizeStats


__all__ = ["NormalizedDoc", "NormalizeStats", "normalize_doc"]
