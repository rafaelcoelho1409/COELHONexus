from __future__ import annotations


# Below this size the monolith path no-ops (idempotent for pre-split corpora).
MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000

# 300 B, not 64 B — micro-fragments leaked through at the lower floor.
SPLIT_MIN_SECTION_BYTES = 300

# Empirical: Dask Changelog (722 KB) splits cleanly, DataFrame/Futures sections
# (104-139 KB, no splittable H2) stay intact. Above ~150 KB → slow rendering;
# below → over-fragments API references.
SPLIT_MAX_SECTION_BYTES = 150_000

SOURCE_MIN_MARKERS = 3
