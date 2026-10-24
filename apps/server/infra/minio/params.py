"""MinIO connection env + S3 client / transport tunables.

Soft env reads: `service.check_configured()` raises one clear error listing the
missing variables, and every client factory calls it before connecting."""
from __future__ import annotations

import os


ENDPOINT   = os.environ.get("MINIO_ENDPOINT", "").strip()
BUCKET     = os.environ.get("MINIO_BUCKET_COELHONEXUS", "")
ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

REGION            = "us-east-1"
SIGNATURE_VERSION = "s3v4"   # MinIO requires v4 (default v2 fails)

# Default client shape (the dd ingestion storage's): MinIO connection pool bumped
# above aioboto3's default 10 so parallel write_many doesn't starve.
MAX_POOL_CONNECTIONS = 32

# Timeouts — both REQUIRED. Without them aiobotocore put_object hangs silently
# at the aiohttp layer under concurrent load (aio-libs/aiobotocore#738 / #451).
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S    = 30

# InternalError, ServiceUnavailable, SlowDown).
MAX_RETRY_ATTEMPTS = 10
RETRY_MODE         = "standard"

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

# Retryable S3 error codes by direction — membership decides retry vs
# fail-fast in the chunk read/write loops.
TRANSIENT_WRITE_CODES = (
    "IncompleteBody",
    "RequestTimeout",
    "InternalError",
    "ServiceUnavailable",
    "SlowDown",
)
TRANSIENT_READ_CODES = (
    "RequestTimeout",
    "InternalError",
    "ServiceUnavailable",
    "SlowDown",
)
