"""chapter_select — bump when greedy policy or pruning rules change to invalidate cached chapter_plan blobs."""
from __future__ import annotations


# v5 (2026-10-13): anchored-family harmonization, Foundations chapter (doc_distill
# `role`), and the LLM oversize split (chapters > 30% of the corpus).
PROMPT_VERSION = "v5-family-split-foundations-2026-10-13"
