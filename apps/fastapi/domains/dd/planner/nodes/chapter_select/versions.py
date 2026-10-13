"""chapter_select — bump when greedy policy or pruning rules change to invalidate cached chapter_plan blobs."""
from __future__ import annotations


# v5 (2026-10-13): anchored-family harmonization, Foundations chapter (doc_distill
# `role`), and the LLM oversize split (chapters > 30% of the corpus).
# v6 (2026-10-13): an LLM oversize split is repaired (forgotten units attach to their nearest
# neighbour, tiny groups merge, duplicates dropped) instead of rejected; one-word titles allowed.
PROMPT_VERSION = "v6-split-repair-2026-10-13"
