"""MinIO async storage adapter — per-framework persistent ingestion artifacts.

Centralized layout: every ingestion of `framework_slug` shares one canonical
path in MinIO so future per-experience-level synth can reuse the corpus
without re-downloading.

Key layout (single bucket, framework-namespaced):

    ingestion/{framework_slug}/manifest.json
    ingestion/{framework_slug}/pages/{idx:04d}-{slug}.md

Idempotent `ensure_bucket()` runs at FastAPI lifespan startup AND at every
Celery worker prefork init so both processes can self-provision against a
fresh MinIO. Singleton accessor (`get_storage()`) lazy-builds on first use.

Aioboto3 quirks worth knowing:
  - signature_version="s3v4" is REQUIRED for MinIO; default v2 fails.
  - connect_timeout + read_timeout MUST be set — without them put_object
    can hang silently at the aiohttp layer under concurrent load
    (aio-libs/aiobotocore#738 / #451, closed unfixed).
  - max_pool_connections bumped above the default 10 so a parallel
    write_many doesn't starve the pool.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Literal, Optional

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


ContentType = Literal[
    "text/markdown",
    "application/json",
    "text/plain",
    "application/octet-stream",
]


# =============================================================================
# Key shape (per-framework, NOT per-run)
# =============================================================================
def framework_prefix(framework_slug: str) -> str:
    return f"ingestion/{framework_slug.strip().strip('/')}/"


def manifest_key(framework_slug: str) -> str:
    return f"{framework_prefix(framework_slug)}manifest.json"


def page_key(framework_slug: str, idx: int, slug: str) -> str:
    """Zero-padded ordinal makes alphabetical MinIO listing equal document
    order, which is what every downstream consumer (inspect UI, synth)
    expects."""
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{framework_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.md"


class MinIOStorage:
    """Async MinIO/S3 storage for per-framework docs distiller artifacts."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._session = aioboto3.Session()
        self._boto_config = Config(
            signature_version="s3v4",
            max_pool_connections=32,
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 10, "mode": "standard"},
        )

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            config=self._boto_config,
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self.bucket)
                logger.info(f"[minio] bucket {self.bucket!r} exists")
                return
            except ClientError as e:
                code = (e.response or {}).get("Error", {}).get("Code", "")
                if code not in ("404", "NoSuchBucket", "NoSuchKey"):
                    raise
            await s3.create_bucket(Bucket=self.bucket)
            logger.info(f"[minio] bucket {self.bucket!r} created")

    async def write(
        self,
        key: str,
        content: str | bytes,
        content_type: ContentType = "text/markdown",
    ) -> int:
        body = content.encode("utf-8") if isinstance(content, str) else content
        for attempt in range(3):
            try:
                async with self._client() as s3:
                    await s3.put_object(
                        Bucket=self.bucket,
                        Key=key,
                        Body=body,
                        ContentType=content_type,
                    )
                return len(body)
            except ClientError as e:
                code = (e.response or {}).get("Error", {}).get("Code", "")
                transient = code in (
                    "IncompleteBody", "RequestTimeout", "InternalError",
                    "ServiceUnavailable", "SlowDown",
                )
                if not transient or attempt == 2:
                    raise
                await asyncio.sleep(0.3 * (3 ** attempt))
        return len(body)

    async def read_text(self, key: str, encoding: str = "utf-8") -> str:
        async with self._client() as s3:
            resp = await s3.get_object(Bucket=self.bucket, Key=key)
            async with resp["Body"] as stream:
                data = await stream.read()
        return data.decode(encoding)

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as e:
                code = (e.response or {}).get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchKey"):
                    return False
                raise

    async def list(self, prefix: str) -> list[str]:
        out: list[str] = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self.bucket, Prefix=prefix,
            ):
                for obj in page.get("Contents") or []:
                    out.append(obj["Key"])
        return out

    async def list_subfolders(self, prefix: str) -> list[str]:
        """Return the immediate sub-prefix names under `prefix` (S3
        delimiter pagination — much cheaper than a full recursive `list`
        when you just want folder names). Returns stripped names; e.g.
        for prefix=`ingestion/` returns `['docker', 'fastapi', ...]`.
        """
        prefix = prefix.rstrip("/") + "/"
        names: list[str] = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self.bucket, Prefix=prefix, Delimiter="/",
            ):
                for cp in page.get("CommonPrefixes") or []:
                    p = cp.get("Prefix") or ""
                    if p.startswith(prefix) and p.endswith("/"):
                        names.append(p[len(prefix):-1])
        return names

    async def copy_object(self, src_key: str, dst_key: str) -> int:
        """Server-side copy within the same bucket. Returns the copied
        object's byte size (used to track snapshot sizes)."""
        async with self._client() as s3:
            await s3.copy_object(
                Bucket=self.bucket,
                Key=dst_key,
                CopySource={"Bucket": self.bucket, "Key": src_key},
            )
            head = await s3.head_object(Bucket=self.bucket, Key=dst_key)
            return int(head.get("ContentLength") or 0)

    async def copy_prefix(
        self, src_prefix: str, dst_prefix: str,
        max_concurrent: int = 16,
        skip_substring: str | None = None,
    ) -> int:
        """Recursively server-side-copy every object under `src_prefix` to
        the matching position under `dst_prefix`. Returns the count of
        objects copied. `skip_substring` lets the caller exclude paths
        (e.g. avoid copying `/_snapshots/` recursively into snapshots).

        Shared client across the loop — same fix as `delete_prefix`."""
        keys = await self.list(src_prefix)
        if skip_substring:
            keys = [k for k in keys if skip_substring not in k]
        if not keys:
            return 0
        sem = asyncio.BoundedSemaphore(max_concurrent)
        async with self._client() as s3:
            async def _one(k: str) -> None:
                rel = k[len(src_prefix):]
                dst = dst_prefix + rel
                async with sem:
                    await s3.copy_object(
                        Bucket=self.bucket,
                        Key=dst,
                        CopySource={"Bucket": self.bucket, "Key": k},
                    )
            await asyncio.gather(*(_one(k) for k in keys))
        return len(keys)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with `prefix`. Parallel
        per-object deletes (sem=32) — the batched `delete_objects` route
        requires a `Content-MD5` header that aiobotocore doesn't send,
        and MinIO rejects it (MissingContentMD5).

        ONE shared client across the loop. Earlier impl opened a fresh
        boto3 session per key → 1000 deletes paid 1000 TCP handshakes;
        single-client + sem matches `_write_chunk` and runs ~30x faster.
        """
        keys = await self.list(prefix)
        if not keys:
            return 0
        sem = asyncio.BoundedSemaphore(32)
        async with self._client() as s3:
            async def _one(k: str) -> None:
                async with sem:
                    await s3.delete_object(Bucket=self.bucket, Key=k)
            await asyncio.gather(*(_one(k) for k in keys))
        return len(keys)

    async def write_many(
        self,
        items: list[tuple[str, str | bytes, ContentType]],
        max_concurrent: int = 16,
        chunk_size: int = 256,
        chunk_timeout_s: float = 60.0,
        max_chunk_retries: int = 3,
    ) -> list[int]:
        if not items:
            return []
        results: list[int] = []
        for start in range(0, len(items), chunk_size):
            chunk = items[start:start + chunk_size]
            end = start + len(chunk)
            last_err: Exception | None = None
            for attempt in range(max_chunk_retries):
                try:
                    chunk_results = await asyncio.wait_for(
                        self._write_chunk(chunk, max_concurrent),
                        timeout=chunk_timeout_s,
                    )
                    results.extend(chunk_results)
                    break
                except (asyncio.TimeoutError, ClientError) as e:
                    last_err = e
                    if isinstance(e, ClientError):
                        code = (e.response or {}).get("Error", {}).get("Code", "")
                        if code not in (
                            "IncompleteBody", "RequestTimeout", "InternalError",
                            "ServiceUnavailable", "SlowDown",
                        ):
                            raise
                if attempt < max_chunk_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
            else:
                raise RuntimeError(
                    f"write_many chunk [{start}:{end}) failed after "
                    f"{max_chunk_retries} attempts; last error: "
                    f"{type(last_err).__name__}: {last_err}"
                )
        return results

    async def _write_chunk(
        self,
        chunk: list[tuple[str, str | bytes, ContentType]],
        max_concurrent: int,
    ) -> list[int]:
        sem = asyncio.BoundedSemaphore(max_concurrent)
        async with self._client() as s3:
            async def _put_one(k: str, c: str | bytes, ct: ContentType) -> int:
                body = c.encode("utf-8") if isinstance(c, str) else c
                async with sem:
                    await s3.put_object(
                        Bucket=self.bucket, Key=k, Body=body, ContentType=ct,
                    )
                return len(body)
            return await asyncio.gather(
                *(_put_one(k, c, ct) for k, c, ct in chunk)
            )


# =============================================================================
# Singleton accessor — used by Store, tier modules, and the runs router
# =============================================================================
_storage: Optional[MinIOStorage] = None


def get_storage() -> MinIOStorage:
    global _storage
    if _storage is None:
        endpoint = os.environ.get("MINIO_ENDPOINT", "").strip()
        if not endpoint:
            raise RuntimeError(
                "MINIO_ENDPOINT env var is unset — required for docs distiller "
                "ingestion page-body storage"
            )
        _storage = MinIOStorage(
            bucket=os.environ.get("MINIO_BUCKET_COELHONEXUS", "coelhonexus"),
            endpoint_url=endpoint,
            access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        )
    return _storage
