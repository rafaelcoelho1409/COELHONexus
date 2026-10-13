"""chapter_select — bump when greedy policy or pruning rules change to invalidate cached chapter_plan blobs."""
from __future__ import annotations


# v5 (2026-10-13): anchored-family harmonization, Foundations chapter (doc_distill
# `role`), and the LLM oversize split (chapters > 30% of the corpus).
# v6 (2026-10-13): an LLM oversize split is repaired (forgotten units attach to their nearest
# neighbour, tiny groups merge, duplicates dropped) instead of rejected; one-word titles allowed.
# v7 (2026-10-13): placement ties (the assigner scores in 0.0/0.3/0.7/1.0 steps) go to the chapter
# the page overlaps most, not to the lowest proposal index (deepagents: 42-page catch-all); an
# oversize chapter is partitioned by average-linkage clustering of the pages' word vectors and the
# LLM only names the groups; the catch-all appendix over 8% of the corpus is split the same way.
PROMPT_VERSION = "v7-cluster-split-2026-10-13"
