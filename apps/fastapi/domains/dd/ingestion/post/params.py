from __future__ import annotations


# Below this size the monolith path no-ops (idempotent for pre-split corpora).
MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000

# Individual pages at/over this size get H2/H3 pre-split in multi-page
# corpora (the monolith path above only fires for single-page corpora, so
# without this Tier 3's giants sailed through dedup untouched). Sized to the
# downstream consumers that motivated it — doc_distill BODY_CHARS_MAX and
# synth digest _MAX_SOURCE_CHARS are both 100_000 — so nothing Planner/Synth
# reads ever truncates silently again. Proven splitter, same
# _size_aware_recursive_split the monolith path uses (atomic fences/tables,
# parent-H1 prepended); pages with no clean split come back intact.
OVERSIZED_SPLIT_BYTES = 100_000

# 300 B, not 64 B — micro-fragments leaked through at the lower floor.
SPLIT_MIN_SECTION_BYTES = 300

# Empirical: Dask Changelog (722 KB) splits cleanly, DataFrame/Futures sections
# (104-139 KB, no splittable H2) stay intact. Above ~150 KB → slow rendering;
# below → over-fragments API references.
SPLIT_MAX_SECTION_BYTES = 150_000

SOURCE_MIN_MARKERS = 3

# 32 amortizes latency without overwhelming the MinIO pool (serial was 75s/1500 pages).
READ_CONCURRENCY = 32
DELETE_CONCURRENCY = 32
