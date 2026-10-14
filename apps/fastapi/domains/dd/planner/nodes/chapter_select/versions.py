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
# v8 (2026-10-13): clustering similarity mixes slug words (module path) + summary words + role
# (leftover pages: intra-group similarity 0.05 on summaries alone → grab-bag groups); the catch-all
# appendix splits into groups of ~10 pages (up to 6) with names judged from all listed pages;
# migration / upgrade / changelog PAGES are pulled out of topical chapters into one migration
# chapter (an existing one, else a new one from 3+ pages).
# v9 (2026-10-14): oversize / catch-all splits use one part per ~20 / ~10 pages (up to 8) with a floor of
# 60% of the average part, and cluster on the page's URL directory too (langfuse 115 integration pages:
# [86, 29] mixed -> six coherent groups; fastmcp 114 leftovers: [49, 19, 27, 19] -> eight); orientation-
# titled chapters under 5 pages are folded into Foundations.
# v10 (2026-10-14): after the split, pages of a catalog directory (integrations/, providers/ ...) that sit in other
# chapters move to the chapter holding most of that directory; split groups are named with their URL sections.
# v11 (2026-10-14): chapters under 4 pages join the chapter that owns their URL directory (description gains "Also covers: ...").
PROMPT_VERSION = "v11-micro-merge-2026-10-14"
