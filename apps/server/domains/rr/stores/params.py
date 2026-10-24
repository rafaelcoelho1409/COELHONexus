"""stores params — MinIO client tuning (no behavior)."""
from __future__ import annotations

from botocore.config import Config


MINIO_BOTO_CONFIG = Config(
    signature_version    = "s3v4",      # MinIO requires v4; default v2 fails
    max_pool_connections = 16,
    connect_timeout      = 5.0,
    read_timeout         = 30.0,
    retries              = {"max_attempts": 3, "mode": "standard"},
)
