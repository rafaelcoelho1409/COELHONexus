"""MinIO (S3) client factories + the shared async object-storage adapter.

The only module that imports `aioboto3` / `botocore` or reads the `MINIO_*` /
`AWS_*` env vars. Everything else reaches MinIO dotted through here:
`get_storage()` (the shared `MinIOStorage` adapter), `new_client()` /
`new_sync_client()` (raw S3 clients for code that needs operations the adapter
does not wrap), `bucket()`, `check_configured()`, and the `ClientError` /
`MinIOStorage` names for `except` clauses and annotations.

Clients are deliberately not cached: each operation opens one, uses it and
closes it (aioboto3 clients are async context managers). Callers pick their own
timeout / retry / pool shape through the keyword arguments."""
from __future__ import annotations
import infra
from . import domain, params

import asyncio
import contextlib
import logging
from typing import Optional

import aioboto3
from botocore.exceptions import ClientError
from botocore.session import get_session


logger = logging.getLogger(__name__)


def check_configured() -> None:
    """Raise one error naming every missing MinIO env var."""
    missing = [
        name for name, value in (
            ("MINIO_ENDPOINT", params.ENDPOINT),
            ("MINIO_BUCKET_COELHONEXUS", params.BUCKET),
            ("AWS_ACCESS_KEY_ID", params.ACCESS_KEY),
            ("AWS_SECRET_ACCESS_KEY", params.SECRET_KEY),
        ) if not value
    ]
    if missing:
        raise RuntimeError(f"[minio] missing env: {', '.join(missing)}")


def bucket() -> str:
    """The COELHO Nexus bucket name (set in Helm values.yaml, propagated via the server configmap)."""
    check_configured()
    return params.BUCKET


_session: Optional[aioboto3.Session] = None


def new_client(
    *, max_pool_connections: int | None = params.MAX_POOL_CONNECTIONS,
    connect_timeout_s: float = params.CONNECT_TIMEOUT_S,
    read_timeout_s: float = params.READ_TIMEOUT_S,
    max_attempts: int = params.MAX_RETRY_ATTEMPTS,
):
    """An aioboto3 s3 async-context-manager client. Use:

        async with infra.minio.service.new_client() as s3:
            await s3.put_object(...)
    """
    global _session
    check_configured()
    if _session is None:
        _session = aioboto3.Session()
    return _session.client(
        "s3",
        endpoint_url = params.ENDPOINT,
        aws_access_key_id = params.ACCESS_KEY,
        aws_secret_access_key = params.SECRET_KEY,
        region_name = params.REGION,
        config = domain.build_boto_config(
            max_pool_connections = max_pool_connections, connect_timeout_s = connect_timeout_s,
            read_timeout_s = read_timeout_s, max_attempts = max_attempts,
        ),
    )


def new_sync_client(
    *, max_pool_connections: int | None = None,
    connect_timeout_s: float = params.CONNECT_TIMEOUT_S,
    read_timeout_s: float = params.READ_TIMEOUT_S,
    max_attempts: int = params.MAX_RETRY_ATTEMPTS,
):
    """A blocking botocore s3 client for sync code paths; the caller must `close()` it."""
    check_configured()
    return get_session().create_client(
        "s3",
        endpoint_url = params.ENDPOINT,
        aws_access_key_id = params.ACCESS_KEY,
        aws_secret_access_key = params.SECRET_KEY,
        region_name = params.REGION,
        config = domain.build_boto_config(
            max_pool_connections = max_pool_connections, connect_timeout_s = connect_timeout_s,
            read_timeout_s = read_timeout_s, max_attempts = max_attempts,
        ),
    )


def span(
    operation: str, *, bucket: str, key: str | None = None,
) -> contextlib.AbstractContextManager:
    """No dedicated OTel auto-instrumentation covers aioboto3/aiobotocore's
    async client (unlike sync botocore, which `opentelemetry-instrumentation-
    botocore` patches) — hand-rolled, mirroring the `aws.s3.*` semconv names
    (https://opentelemetry.io/docs/specs/semconv/object-stores/s3/) even
    though this is MinIO, not AWS, for interoperability with S3-shaped
    tooling/dashboards.

    `key` is optional — batch operations (write_many, copy_prefix, ...) span
    many different keys at once, so there's no single one to report; those
    call sites instead set a `*_count` attribute on the yielded span."""
    return infra.opentelemetry.service.client_span(
        f"aws.s3.{operation}", {"aws.s3.bucket": bucket, "aws.s3.key": key},
    )


class MinIOStorage:
    """Async MinIO/S3 storage for docs-distiller artifacts."""
    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = params.REGION,
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._session = aioboto3.Session()
        self._boto_config = domain.build_boto_config(
            max_pool_connections = params.MAX_POOL_CONNECTIONS,
            connect_timeout_s = params.CONNECT_TIMEOUT_S,
            read_timeout_s = params.READ_TIMEOUT_S,
            max_attempts = params.MAX_RETRY_ATTEMPTS,
        )

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url = self.endpoint_url,
            aws_access_key_id = self._access_key,
            aws_secret_access_key = self._secret_key,
            region_name = self._region,
            config = self._boto_config,
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket = self.bucket)
                logger.info(f"[minio] bucket {self.bucket!r} exists")
                return
            except ClientError as e:
                code = (e.response or {}).get("Error", {}).get("Code", "")
                if code not in ("404", "NoSuchBucket", "NoSuchKey"):
                    raise
            await s3.create_bucket(Bucket = self.bucket)
            logger.info(f"[minio] bucket {self.bucket!r} created")

    async def write(
        self,
        key: str,
        content: str | bytes,
        content_type: str = "text/markdown",
    ) -> int:
        body = content.encode("utf-8") if isinstance(content, str) else content
        with span("put_object", bucket = self.bucket, key = key) as sp:
            for attempt in range(3):
                try:
                    async with self._client() as s3:
                        await s3.put_object(
                            Bucket = self.bucket,
                            Key = key,
                            Body = body,
                            ContentType = content_type,
                        )
                    if sp is not None:
                        sp.set_attribute("aws.s3.write_bytes", len(body))
                    return len(body)
                except ClientError as e:
                    code = (e.response or {}).get("Error", {}).get("Code", "")
                    if code not in params.TRANSIENT_WRITE_CODES or attempt == 2:
                        raise
                    await asyncio.sleep(0.3 * (3 ** attempt))
        return len(body)

    async def read_text(self, key: str, encoding: str = "utf-8") -> str:
        with span("get_object", bucket = self.bucket, key = key):
            async with self._client() as s3:
                resp = await s3.get_object(Bucket = self.bucket, Key = key)
                async with resp["Body"] as stream:
                    data = await stream.read()
        return data.decode(encoding)

    async def read_bytes(self, key: str) -> bytes:
        """Raw binary read (.npz embeddings, cluster matrices)."""
        with span("get_object", bucket = self.bucket, key = key):
            async with self._client() as s3:
                resp = await s3.get_object(Bucket = self.bucket, Key = key)
                async with resp["Body"] as stream:
                    return await stream.read()

    async def delete(self, key: str) -> None:
        with span("delete_object", bucket = self.bucket, key = key):
            async with self._client() as s3:
                await s3.delete_object(Bucket = self.bucket, Key = key)

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket = self.bucket, Key = key)
                return True
            except ClientError as e:
                code = (e.response or {}).get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchKey"):
                    return False
                raise

    async def list(self, prefix: str) -> list[str]:
        out: list[str] = []
        with span("list_objects_v2", bucket = self.bucket, key = prefix) as sp:
            async with self._client() as s3:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket = self.bucket, Prefix = prefix):
                    for obj in page.get("Contents") or []:
                        out.append(obj["Key"])
            if sp is not None:
                sp.set_attribute("aws.s3.list_count", len(out))
        return out

    async def list_subfolders(self, prefix: str) -> list[str]:
        """Immediate sub-prefix names (delimiter pagination, cheaper than recursive)."""
        prefix = prefix.rstrip("/") + "/"
        names: list[str] = []
        with span("list_objects_v2_delimited", bucket = self.bucket, key = prefix) as sp:
            async with self._client() as s3:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket = self.bucket, Prefix = prefix, Delimiter = "/",
                ):
                    for cp in page.get("CommonPrefixes") or []:
                        p = cp.get("Prefix") or ""
                        if p.startswith(prefix) and p.endswith("/"):
                            names.append(p[len(prefix):-1])
            if sp is not None:
                sp.set_attribute("aws.s3.list_count", len(names))
        return names

    async def copy_object(self, src_key: str, dst_key: str) -> int:
        """Server-side copy within the same bucket; returns dst byte size."""
        with span("copy_object", bucket = self.bucket, key = dst_key) as sp:
            if sp is not None:
                sp.set_attribute("aws.s3.copy_source", f"{self.bucket}/{src_key}")
            async with self._client() as s3:
                await s3.copy_object(
                    Bucket = self.bucket,
                    Key = dst_key,
                    CopySource = {"Bucket": self.bucket, "Key": src_key},
                )
                head = await s3.head_object(Bucket = self.bucket, Key = dst_key)
                return int(head.get("ContentLength") or 0)

    async def copy_prefix(
        self, src_prefix: str, dst_prefix: str,
        max_concurrent: int = params.COPY_MAX_CONCURRENT,
        skip_substring: str | None = None,
    ) -> int:
        """Recursive server-side copy. `skip_substring` excludes paths
        (e.g. `/_snapshots/`). Shared client across the loop (see delete_prefix)."""
        with span("copy_prefix_batch", bucket = self.bucket, key = dst_prefix) as sp:
            if sp is not None:
                sp.set_attribute("aws.s3.copy_source", f"{self.bucket}/{src_prefix}")
            keys = await self.list(src_prefix)
            if skip_substring:
                keys = [k for k in keys if skip_substring not in k]
            if sp is not None:
                sp.set_attribute("aws.s3.copy_count", len(keys))
            if not keys:
                return 0
            sem = asyncio.BoundedSemaphore(max_concurrent)
            async with self._client() as s3:
                async def _one(k: str) -> None:
                    rel = k[len(src_prefix):]
                    dst = dst_prefix + rel
                    async with sem:
                        await s3.copy_object(
                            Bucket = self.bucket,
                            Key = dst,
                            CopySource = {"Bucket": self.bucket, "Key": k},
                        )
                await asyncio.gather(*(_one(k) for k in keys))
            return len(keys)

    async def delete_prefix(self, prefix: str) -> int:
        """Parallel per-object deletes (batched delete_objects needs Content-MD5
        which aiobotocore doesn't send → MinIO rejects MissingContentMD5).
        Shared client across the loop — ~30× faster than per-key sessions."""
        with span("delete_objects_batch", bucket = self.bucket, key = prefix) as sp:
            keys = await self.list(prefix)
            if sp is not None:
                sp.set_attribute("aws.s3.delete_count", len(keys))
            if not keys:
                return 0
            sem = asyncio.BoundedSemaphore(params.DELETE_MAX_CONCURRENT)
            async with self._client() as s3:
                async def _one(k: str) -> None:
                    async with sem:
                        await s3.delete_object(Bucket = self.bucket, Key = k)
                await asyncio.gather(*(_one(k) for k in keys))
            return len(keys)

    async def write_many(
        self,
        items: list[tuple[str, str | bytes, str]],
        max_concurrent: int = params.WRITE_MAX_CONCURRENT,
        chunk_size: int = params.WRITE_CHUNK_SIZE,
        chunk_timeout_s: float = params.WRITE_CHUNK_TIMEOUT_S,
        max_chunk_retries: int = params.WRITE_MAX_CHUNK_RETRIES,
    ) -> list[int]:
        if not items:
            return []
        with span("put_object_batch", bucket = self.bucket) as sp:
            if sp is not None:
                sp.set_attribute("aws.s3.write_count", len(items))
            results: list[int] = []
            for start in range(0, len(items), chunk_size):
                chunk = items[start:start + chunk_size]
                end = start + len(chunk)
                last_err: Exception | None = None
                for attempt in range(max_chunk_retries):
                    try:
                        chunk_results = await asyncio.wait_for(
                            self._write_chunk(chunk, max_concurrent),
                            timeout = chunk_timeout_s,
                        )
                        results.extend(chunk_results)
                        break
                    except (asyncio.TimeoutError, ClientError) as e:
                        last_err = e
                        if isinstance(e, ClientError):
                            code = (e.response or {}).get("Error", {}).get("Code", "")
                            if code not in params.TRANSIENT_WRITE_CODES:
                                raise
                    if attempt < max_chunk_retries - 1:
                        await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    raise RuntimeError(
                        f"write_many chunk [{start}:{end}) failed after "
                        f"{max_chunk_retries} attempts; last error: "
                        f"{type(last_err).__name__}: {last_err}"
                    )
            if sp is not None:
                sp.set_attribute("aws.s3.write_bytes", sum(results))
        return results

    async def _write_chunk(
        self,
        chunk: list[tuple[str, str | bytes, str]],
        max_concurrent: int,
    ) -> list[int]:
        sem = asyncio.BoundedSemaphore(max_concurrent)
        async with self._client() as s3:
            async def _put_one(k: str, c: str | bytes, ct: str) -> int:
                body = c.encode("utf-8") if isinstance(c, str) else c
                async with sem:
                    await s3.put_object(
                        Bucket = self.bucket, Key = k, Body = body, ContentType = ct,
                    )
                return len(body)
            return await asyncio.gather(
                *(_put_one(k, c, ct) for k, c, ct in chunk)
            )

    async def read_many(
        self,
        keys: list[str],
        max_concurrent: int = params.READ_MAX_CONCURRENT,
        chunk_size: int = params.READ_CHUNK_SIZE,
        chunk_timeout_s: float = params.READ_CHUNK_TIMEOUT_S,
        max_chunk_retries: int = params.READ_MAX_CHUNK_RETRIES,
        encoding: str = "utf-8",
    ) -> list[str]:
        """Parallel chunked read; one shared client per chunk (TLS+SigV4 cost).
        Returns bodies in input order."""
        if not keys:
            return []
        with span("get_object_batch", bucket = self.bucket) as sp:
            if sp is not None:
                sp.set_attribute("aws.s3.read_count", len(keys))
            results: list[str] = []
            for start in range(0, len(keys), chunk_size):
                chunk = keys[start:start + chunk_size]
                end = start + len(chunk)
                last_err: Exception | None = None
                for attempt in range(max_chunk_retries):
                    try:
                        chunk_results = await asyncio.wait_for(
                            self._read_chunk(chunk, max_concurrent, encoding),
                            timeout = chunk_timeout_s,
                        )
                        results.extend(chunk_results)
                        break
                    except (asyncio.TimeoutError, ClientError) as e:
                        last_err = e
                        if isinstance(e, ClientError):
                            code = (e.response or {}).get("Error", {}).get("Code", "")
                            if code not in params.TRANSIENT_READ_CODES:
                                raise
                        logger.warning(
                            f"[minio] read_many chunk [{start}:{end}) attempt "
                            f"{attempt+1}/{max_chunk_retries} transient — retrying"
                        )
                    if attempt < max_chunk_retries - 1:
                        await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    raise RuntimeError(
                        f"read_many chunk [{start}:{end}) failed after "
                        f"{max_chunk_retries} attempts; last error: "
                        f"{type(last_err).__name__}: {last_err}"
                    )
        return results

    async def _read_chunk(
        self,
        chunk: list[str],
        max_concurrent: int,
        encoding: str,
    ) -> list[str]:
        sem = asyncio.BoundedSemaphore(max_concurrent)
        async with self._client() as s3:
            async def _get_one(key: str) -> str:
                async with sem:
                    resp = await s3.get_object(Bucket = self.bucket, Key = key)
                    async with resp["Body"] as stream:
                        data = await stream.read()
                return data.decode(encoding)
            return await asyncio.gather(*(_get_one(k) for k in chunk))


_storage: Optional[MinIOStorage] = None


def get_storage() -> MinIOStorage:
    """Singleton; fails loudly on missing config."""
    global _storage
    if _storage is None:
        check_configured()
        _storage = MinIOStorage(
            bucket       = params.BUCKET,
            endpoint_url = params.ENDPOINT,
            access_key   = params.ACCESS_KEY,
            secret_key   = params.SECRET_KEY,
        )
    return _storage


# Public surface for the rest of the code (the re-exports are intentional).
__all__ = [
    "ClientError", "MinIOStorage", "bucket", "check_configured", "get_storage", "new_client",
    "new_sync_client", "span",
]
