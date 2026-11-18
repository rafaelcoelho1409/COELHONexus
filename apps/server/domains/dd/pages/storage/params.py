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
# What a key contains when it lies inside the snapshots subtree.
SNAPSHOTS_SKIP_SUBSTRING = f"/{SNAPSHOTS_SUBDIR}"

# A snapshot's id is the UTC time it was taken at, in this format, so listing them alphabetically is listing them in time order.
SNAPSHOT_TS_FORMAT = "%Y%m%dT%H%M%SZ"

# Deleting a framework's objects, at most this many at a time.
DELETE_CONCURRENCY = 32

# Pure-Python text work (normalizing, sentinelizing a vault) on a multi-megabyte bundle takes tens of seconds. Run inline it would
# hold the event loop that serves the cancel watcher and the progress heartbeat; in a thread the loop keeps turning. Text shorter
# than this stays inline — a thread costs more than it does.
CPU_THREAD_THRESHOLD_CHARS = 200_000

# What the Store writes: pages and vault text are Markdown, manifests and vault manifests are JSON (the stored manifest compact).
MARKDOWN_TYPE = "text/markdown"
JSON_TYPE = "application/json"
MANIFEST_JSON_SEPARATORS = (",", ":")

# The client that downloads a page's images and media into the store (one per Store, so its keep-alive sockets are reused).
ARTIFACT_USER_AGENT = "COELHONexus-DocsDistiller-Artifacts/1.0"
ARTIFACT_ACCEPT = "image/*, video/*, audio/*, */*;q=0.5"
ARTIFACT_TIMEOUT_S = 30.0
ARTIFACT_CONNECT_TIMEOUT_S = 10.0
ARTIFACT_MAX_CONNECTIONS = 20
ARTIFACT_MAX_KEEPALIVE = 10

# Titles worked out for manifests written before pages were titled at ingestion are kept in memory for this many (framework, run)
# pairs, and a manifest's untitled pages are read this many at a time.
TITLE_CACHE_MAX = 16
TITLE_READ_CONCURRENCY = 16
