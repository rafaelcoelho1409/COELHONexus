from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class StorageLayout:
    # MinIO object paths. Unchanged from the previous layout on
    # purpose — renaming the code package must not orphan already-persisted
    # `credentials.enc` / `kek.key` / `settings.json` blobs.
    credentials: str = "llm/credentials.enc"
    kek:         str = "llm/kek.key"
    settings:    str = "llm/settings.json"


STORAGE = StorageLayout()


@dataclass(frozen = True, slots = True)
class MinioClientConfig:
    """S3 client shape for the credential/settings blobs — one concept,
    tuned together (short timeouts + single attempt so a dead MinIO fails
    fast into the env fallback instead of stalling callers)."""
    signature_version: str = "s3v4"
    region:            str = "us-east-1"
    connect_timeout_s: int = 3
    read_timeout_s:    int = 5
    max_attempts:      int = 1
    retry_mode:        str = "standard"


MINIO_CLIENT = MinioClientConfig()
