from __future__ import annotations


# Redis TTL on the live (in-flight) manifest snapshot. Aligned with progress.TTL_S.
TTL_S = 7200

# Per-Store live-manifest write throttle — without it every add_page serializes
# the FULL growing manifest to Redis (≈300 KB at 1500 pages); compounded writes
# blew past Celery's 30-min soft_time_limit on Docker's Tier 3 run.
LIVE_MANIFEST_THROTTLE_S = 1.0

# Snapshot subdirectory name. Kept under each framework_prefix so snapshots
# travel with the framework but are excluded from `take()` / `restore()` body
# operations to avoid recursive snapshotting.
SNAPSHOTS_SUBDIR = "_snapshots/"
