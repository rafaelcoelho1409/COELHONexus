"""MinIO I/O for RR — digest.json + per-paper extraction.json artifacts.

Per docs/CODE-CONVENTIONS.md §service: async + I/O. Bucket is created
idempotently via `bootstrap_minio()` at FastAPI lifespan startup
(architecture doc §2.4.4).

Bucket reuse: all RR artifacts share the existing `coelhonexus` bucket
(env var MINIO_BUCKET_COELHONEXUS) — same one DD + YCS write to. The
`rr/` prefix namespaces it.

Session model: one `aioboto3.Session()` instance reused per process;
clients are opened per-operation via `session.client(...)` async context
managers (idiomatic aioboto3).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..keys import (
    MINIO_PREFIX_RR,
    digest_minio_key,
    extraction_minio_key,
)
from ..params import STORES_PARAMS


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Session / config — one Session per process; clients are per-operation
# --------------------------------------------------------------------------- #
_session: Optional[aioboto3.Session] = None
_boto_config = Config(
    signature_version    = "s3v4",      # MinIO requires v4; default v2 fails
    max_pool_connections = 16,
    connect_timeout      = 5.0,
    read_timeout         = 30.0,
    retries              = {"max_attempts": 3, "mode": "standard"},
)


def _get_session() -> aioboto3.Session:
    global _session
    if _session is None:
        _session = aioboto3.Session()
    return _session


def _bucket() -> str:
    """Resolve the bucket name from env (set in Helm values.yaml +
    propagated via the fastapi configmap)."""
    return os.environ["MINIO_BUCKET_COELHONEXUS"]


def _endpoint() -> str:
    return os.environ["MINIO_ENDPOINT"].strip()


def _client():
    """An aioboto3 s3 async-context-manager client. Use:

        async with _client() as s3:
            await s3.put_object(...)
    """
    return _get_session().client(
        "s3",
        endpoint_url          = _endpoint(),
        aws_access_key_id     = os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name           = "us-east-1",
        config                = _boto_config,
    )


# --------------------------------------------------------------------------- #
# Bootstrap — ensure the bucket exists. Idempotent.
# --------------------------------------------------------------------------- #
async def bootstrap_minio() -> None:
    """head_bucket; create_bucket on 404. Same pattern as the dd ingestion
    storage's ensure_bucket()."""
    bucket = _bucket()
    async with _client() as s3:
        try:
            await s3.head_bucket(Bucket=bucket)
            logger.info(
                f"[rr-minio] bucket {bucket!r} exists "
                f"(prefix={MINIO_PREFIX_RR!r})"
            )
            return
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchBucket", "NoSuchKey"):
                raise
        await s3.create_bucket(Bucket=bucket)
        logger.info(f"[rr-minio] created bucket {bucket!r}")


# --------------------------------------------------------------------------- #
# Digest JSON — the final ranked digest for a scan
# --------------------------------------------------------------------------- #
async def put_digest_json(scan_id: str, payload: dict[str, Any]) -> str:
    """Write the scan's digest snapshot. Returns the MinIO key."""
    key  = digest_minio_key(scan_id)
    body = json.dumps(payload, default=str).encode("utf-8")
    async with _client() as s3:
        await s3.put_object(
            Bucket      = _bucket(),
            Key         = key,
            Body        = body,
            ContentType = STORES_PARAMS.minio_json_content_type,
        )
    return key


async def get_digest_json(scan_id: str) -> dict[str, Any] | None:
    """Read the digest snapshot. Returns None on 404."""
    key = digest_minio_key(scan_id)
    async with _client() as s3:
        try:
            obj = await s3.get_object(Bucket=_bucket(), Key=key)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return None
            raise
        body = await obj["Body"].read()
    return json.loads(body)


async def delete_digest_json(scan_id: str) -> bool:
    """Remove the digest object for one scan. Idempotent — returns True if
    the object was present, False if it wasn't. Other errors raise.

    Caller: `service.delete_scan` (the per-row delete affordance in the
    Recent-scans dropdown)."""
    key = digest_minio_key(scan_id)
    async with _client() as s3:
        try:
            await s3.delete_object(Bucket=_bucket(), Key=key)
            return True
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
            raise


# --------------------------------------------------------------------------- #
# Extraction JSON — per-paper deep_read output (step 4)
# --------------------------------------------------------------------------- #
async def put_extraction_json(
    scan_id: str, arxiv_id: str, payload: dict[str, Any]
) -> str:
    """Write a deep_read extraction for one paper. Returns the MinIO key."""
    key  = extraction_minio_key(scan_id, arxiv_id)
    body = json.dumps(payload, default=str).encode("utf-8")
    async with _client() as s3:
        await s3.put_object(
            Bucket      = _bucket(),
            Key         = key,
            Body        = body,
            ContentType = STORES_PARAMS.minio_json_content_type,
        )
    return key


async def get_extraction_json(
    scan_id: str, arxiv_id: str,
) -> dict[str, Any] | None:
    """Read an extraction. Returns None on 404."""
    key = extraction_minio_key(scan_id, arxiv_id)
    async with _client() as s3:
        try:
            obj = await s3.get_object(Bucket=_bucket(), Key=key)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return None
            raise
        body = await obj["Body"].read()
    return json.loads(body)


