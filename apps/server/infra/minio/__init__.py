"""infra/minio — MinIO (S3) connection config, client factories and the shared object-storage adapter.

Every domain and the worker bootstrap reach MinIO through here; no other module
imports `aioboto3` / `botocore`, reads the `MINIO_*` / `AWS_*` env vars or
hardcodes an S3 client setting. Domains keep owning what they store (key
layouts, content types, per-domain manifests)."""
from . import domain, params, service


__all__ = ["domain", "params", "service"]
