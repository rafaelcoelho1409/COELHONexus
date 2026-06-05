"""chapter_select tunables — greedy coverage thresholds + cache tag."""
from __future__ import annotations


BLOB_PREFIX = "planner"

# Greedy coverage tuning.
CONFIDENCE_THRESHOLD = 0.5
COVERAGE_TARGET      = 0.95
MIN_DOCS_PER_CHAPTER = 3   # prune chapters below this unless pinned
MIN_KEPT_CHAPTERS    = 3   # restore lowest-pruned if kept < this
