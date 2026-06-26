from __future__ import annotations


# Redis TTL on the live (in-flight) manifest snapshot. Aligned with progress.TTL_S.
TTL_S = 7200

# MinIO connection pool — bumped above aioboto3's default 10 so parallel
# write_many doesn't starve.
MAX_POOL_CONNECTIONS = 32

# Timeouts — both REQUIRED. Without them aiobotocore put_object hangs silently
# at the aiohttp layer under concurrent load (aio-libs/aiobotocore#738 / #451).
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S    = 30

# InternalError, ServiceUnavailable, SlowDown).
MAX_RETRY_ATTEMPTS = 10

# Per-Store live-manifest write throttle — without it every add_page serializes
# the FULL growing manifest to Redis (≈300 KB at 1500 pages); compounded writes
# blew past Celery's 30-min soft_time_limit on Docker's Tier 3 run.
LIVE_MANIFEST_THROTTLE_S = 1.0

# write_many / read_many chunked transport defaults.
WRITE_MAX_CONCURRENT = 16
WRITE_CHUNK_SIZE     = 256
WRITE_CHUNK_TIMEOUT_S = 60.0
WRITE_MAX_CHUNK_RETRIES = 3

READ_MAX_CONCURRENT  = 16
READ_CHUNK_SIZE      = 256
READ_CHUNK_TIMEOUT_S = 60.0
READ_MAX_CHUNK_RETRIES = 3

# delete_prefix / copy_prefix semaphore — single shared client across the loop
# matches `_write_chunk` and runs ~30× faster than per-key sessions.
DELETE_MAX_CONCURRENT = 32
COPY_MAX_CONCURRENT   = 16

# Snapshot subdirectory name. Kept under each framework_prefix so snapshots
# travel with the framework but are excluded from `take()` / `restore()` body
# operations to avoid recursive snapshotting.
SNAPSHOTS_SUBDIR = "_snapshots/"
