"""vault — sentinel-format / hash-algo version markers (cache-invalidation
knobs; bump on any sentinel-shape change so MinIO-cached vaults from prior
versions invalidate cleanly)."""
from __future__ import annotations


SENTINEL_FORMAT_VERSION = 1
HASH_ALGO = "sha256-16"
