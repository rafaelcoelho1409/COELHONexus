"""Credential-store fetch bounds — per docs/CODE-CONVENTIONS.md §2.

Loose scalars, each bounding a different store call. Startup calls
`inject_user_keys_into_env()` synchronously, so every wait here is
capped: a slow/unreachable MinIO degrades to env-only mode, never
blocks the pod past the hard backstop.
"""
from __future__ import annotations


# Botocore per-call timeouts for the MinIO GETs.
MINIO_CONNECT_TIMEOUT_S: int = 3
MINIO_READ_TIMEOUT_S: int = 5

# Wall-clock backstop around the whole blocking fetch (throwaway thread +
# timeout — covers stalls like DNS that outlive botocore's own timeouts).
MINIO_HARD_TIMEOUT_S: int = 8
