from __future__ import annotations

import re


# USC sample count — N=3 is the sweet spot per Wang 2025 / Chen 2023.
_N_SAMPLES = 3
# Representative docs per cluster — Tutmaier 2025 Approach 3 sweet spot.
_REP_DOCS_PER_CLUSTER = 8
# First-N chars per rep doc. Doc intros are highest signal density.
_REP_DOC_CHARS = 500
# c-TF-IDF keyword count per cluster (top distinctive terms).
_KEYWORDS_TOP_K = 20
# Per-call LLM budget. JSON + 1-sentence rationale + 2 alternates fits.
_MAX_TOKENS = 120
# Mild temperature — temp=0 causes sibling clusters to collide on
# generic labels like "Configuration" twice (research-confirmed).
_TEMPERATURE = 0.3
# Parallel cluster-label calls.
_CONCURRENCY = 8
# c-TF-IDF doc-text cap (matches refine.py for cross-step consistency).
_CTFIDF_DOC_CHARS = 1200
# Cache version — bump on prompt redesign so old blobs invalidate.
_PROMPT_VERSION = "v1-2026-05-18"
_BLOB_PREFIX = "planner"

# Hardcoded label for the HDBSCAN noise cluster (-1). NEVER ask the
# LLM to name noise — Tutmaier 2025 found it hallucinates coherence.
_NOISE_LABEL = "Unclustered"

_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)
# Strip wrappers the LM may emit despite the prompt — markdown emphasis,
# "Title:" / "Chapter N:" / "Label:" prefixes, leading punctuation runs.
# Same pattern as v1's classical_map.py (proven battle-tested).
_LEADING_LABEL_RE = re.compile(
    r"^\s*"
    r"(?:\*+\s*)?"
    r"(?:chapter\s+\d+\s*[:\-.]?\s*)?"
    r"(?:(?:title|label|topic|name|cluster)\s*[:\-]\s*)?"
    r"[:*\-.]*\s*",
    re.IGNORECASE,
)
