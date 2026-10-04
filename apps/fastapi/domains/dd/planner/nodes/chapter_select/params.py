"""chapter_select tunables — greedy coverage thresholds + cache tag."""
from __future__ import annotations


BLOB_PREFIX = "planner"

# Greedy coverage tuning.
CONFIDENCE_THRESHOLD = 0.5
COVERAGE_TARGET      = 0.95
MIN_DOCS_PER_CHAPTER = 3   # prune chapters below this unless pinned
MIN_KEPT_CHAPTERS    = 3   # restore lowest-pruned if kept < this

# Placement consolidation — nothing may fall out of the plan silently.
# A "family" is one parent documentation page (all virtual sub-pages of a
# Sphinx autodoc page share its URL).
TORN_FAMILY_MIN_DOCS      = 8      # families smaller than this are never re-homed wholesale
TORN_FAMILY_TOP2_SHARE    = 0.90   # two chapters must hold ≥ this share of the family …
TORN_FAMILY_MIN_MINOR     = 0.25   # … and the smaller of them ≥ this share (a genuine split)
FAMILY_DOMINANCE          = 0.50   # a weak/unplaced doc follows its family's chapter above this share …
FAMILY_MIN_STRONG_SHARE   = 0.50   # … but only if at least this share of the whole family is confidently placed
