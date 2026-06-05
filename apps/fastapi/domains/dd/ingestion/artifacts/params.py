from __future__ import annotations


# Cap per asset — protects single-node K3s memory. Real-world docs ship inline
# notebook-output PNGs up to ~4 MB (UMAP basic_usage.html).
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024

# Drops tracking-pixel-sized payloads.
MIN_ARTIFACT_BYTES = 32

TIMEOUT_S = 30.0

# Shares tier-4's httpx AsyncClient — keeps connection pool + DNS cache warm.
CONCURRENCY = 8
