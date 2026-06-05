"""MinIO adapter + per-framework Store. Re-runs share one canonical prefix.

Manifests: Redis `dd:runs:{run_id}:manifest` (live, by run_id) vs MinIO
`ingestion/{slug}/manifest.json` (canonical, by framework_slug). Idempotent
`ensure_bucket()` runs from FastAPI lifespan + Celery prefork.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, fields
from typing import Optional

import aioboto3
import httpx
import redis.asyncio as redis_aio
from botocore.config import Config
from botocore.exceptions import ClientError

from ..artifacts import extract_and_save_artifacts_from_md
from ...synth.corpus_normalize import normalize_doc
from ...synth.vault import build_manifest
from .entities import ContentType, ManifestEntry
from .keys import (
    artifact_key,
    framework_prefix,
    live_manifest_key,
    manifest_key,
    page_key,
    raw_page_key,
    vault_manifest_key,
    vault_sentinelized_key,
)
from .params import (
    CONNECT_TIMEOUT_S,
    COPY_MAX_CONCURRENT,
    DELETE_MAX_CONCURRENT,
    LIVE_MANIFEST_THROTTLE_S,
    MAX_POOL_CONNECTIONS,
    MAX_RETRY_ATTEMPTS,
    READ_CHUNK_SIZE,
    READ_CHUNK_TIMEOUT_S,
    READ_MAX_CHUNK_RETRIES,
    READ_MAX_CONCURRENT,
    READ_TIMEOUT_S,
    TTL_S,
    WRITE_CHUNK_SIZE,
    WRITE_CHUNK_TIMEOUT_S,
    WRITE_MAX_CHUNK_RETRIES,
    WRITE_MAX_CONCURRENT,
)


logger = logging.getLogger(__name__)


_TRANSIENT_WRITE_CODES = (
    "IncompleteBody", 
    "RequestTimeout", 
    "InternalError",
    "ServiceUnavailable", 
    "SlowDown",
)
_TRANSIENT_READ_CODES = (
    "RequestTimeout", 
    "InternalError", 
    "ServiceUnavailable", 
    "SlowDown",
)


class MinIOStorage:
    """Async MinIO/S3 storage for docs-distiller artifacts."""
    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._session = aioboto3.Session()
        # MinIO requires s3v4 (default v2 fails).
        self._boto_config = Config(
            signature_version = "s3v4",
            max_pool_connections = MAX_POOL_CONNECTIONS,
            connect_timeout = CONNECT_TIMEOUT_S,
            read_timeout = READ_TIMEOUT_S,
            retries = {"max_attempts": MAX_RETRY_ATTEMPTS, "mode": "standard"},
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
        content_type: ContentType = "text/markdown",
    ) -> int:
        body = content.encode("utf-8") if isinstance(content, str) else content
        for attempt in range(3):
            try:
                async with self._client() as s3:
                    await s3.put_object(
                        Bucket = self.bucket,
                        Key = key,
                        Body = body,
                        ContentType = content_type,
                    )
                return len(body)
            except ClientError as e:
                code = (e.response or {}).get("Error", {}).get("Code", "")
                if code not in _TRANSIENT_WRITE_CODES or attempt == 2:
                    raise
                await asyncio.sleep(0.3 * (3 ** attempt))
        return len(body)

    async def read_text(self, key: str, encoding: str = "utf-8") -> str:
        async with self._client() as s3:
            resp = await s3.get_object(Bucket = self.bucket, Key = key)
            async with resp["Body"] as stream:
                data = await stream.read()
        return data.decode(encoding)

    async def read_bytes(self, key: str) -> bytes:
        """Raw binary read (.npz embeddings, cluster matrices)."""
        async with self._client() as s3:
            resp = await s3.get_object(Bucket = self.bucket, Key = key)
            async with resp["Body"] as stream:
                return await stream.read()

    async def delete(self, key: str) -> None:
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
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket = self.bucket, Prefix = prefix):
                for obj in page.get("Contents") or []:
                    out.append(obj["Key"])
        return out

    async def list_subfolders(self, prefix: str) -> list[str]:
        """Immediate sub-prefix names (delimiter pagination, cheaper than recursive)."""
        prefix = prefix.rstrip("/") + "/"
        names: list[str] = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket = self.bucket, Prefix = prefix, Delimiter = "/",
            ):
                for cp in page.get("CommonPrefixes") or []:
                    p = cp.get("Prefix") or ""
                    if p.startswith(prefix) and p.endswith("/"):
                        names.append(p[len(prefix):-1])
        return names

    async def copy_object(self, src_key: str, dst_key: str) -> int:
        """Server-side copy within the same bucket; returns dst byte size."""
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
        max_concurrent: int = COPY_MAX_CONCURRENT,
        skip_substring: str | None = None,
    ) -> int:
        """Recursive server-side copy. `skip_substring` excludes paths
        (e.g. `/_snapshots/`). Shared client across the loop (see delete_prefix)."""
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
        keys = await self.list(prefix)
        if not keys:
            return 0
        sem = asyncio.BoundedSemaphore(DELETE_MAX_CONCURRENT)
        async with self._client() as s3:
            async def _one(k: str) -> None:
                async with sem:
                    await s3.delete_object(Bucket = self.bucket, Key = k)
            await asyncio.gather(*(_one(k) for k in keys))
        return len(keys)

    async def write_many(
        self,
        items: list[tuple[str, str | bytes, ContentType]],
        max_concurrent: int = WRITE_MAX_CONCURRENT,
        chunk_size: int = WRITE_CHUNK_SIZE,
        chunk_timeout_s: float = WRITE_CHUNK_TIMEOUT_S,
        max_chunk_retries: int = WRITE_MAX_CHUNK_RETRIES,
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
                        timeout = chunk_timeout_s,
                    )
                    results.extend(chunk_results)
                    break
                except (asyncio.TimeoutError, ClientError) as e:
                    last_err = e
                    if isinstance(e, ClientError):
                        code = (e.response or {}).get("Error", {}).get("Code", "")
                        if code not in _TRANSIENT_WRITE_CODES:
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
                        Bucket = self.bucket, Key = k, Body = body, ContentType = ct,
                    )
                return len(body)
            return await asyncio.gather(
                *(_put_one(k, c, ct) for k, c, ct in chunk)
            )

    async def read_many(
        self,
        keys: list[str],
        max_concurrent: int = READ_MAX_CONCURRENT,
        chunk_size: int = READ_CHUNK_SIZE,
        chunk_timeout_s: float = READ_CHUNK_TIMEOUT_S,
        max_chunk_retries: int = READ_MAX_CHUNK_RETRIES,
        encoding: str = "utf-8",
    ) -> list[str]:
        """Parallel chunked read; one shared client per chunk (TLS+SigV4 cost).
        Returns bodies in input order."""
        if not keys:
            return []
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
                        if code not in _TRANSIENT_READ_CODES:
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
    """Singleton; strict env reads fail loudly on missing config."""
    global _storage
    if _storage is None:
        _storage = MinIOStorage(
            bucket      = os.environ["MINIO_BUCKET_COELHONEXUS"],
            endpoint_url= os.environ["MINIO_ENDPOINT"].strip(),
            access_key  = os.environ["AWS_ACCESS_KEY_ID"],
            secret_key  = os.environ["AWS_SECRET_ACCESS_KEY"],
        )
    return _storage


class Store:
    """Per-framework store, tagged with run_id for live-progress reads.

    Bodies → canonical MinIO path (re-runs overwrite in place). Redis manifest
    keyed by run_id (live); canonical MinIO manifest written by finalize()."""
    def __init__(
        self,
        run_id: str,
        framework_slug: str,
        r: redis_aio.Redis,
        minio: MinIOStorage,
    ) -> None:
        self.run_id = run_id
        self.framework_slug = framework_slug
        self.r = r
        self.minio = minio
        self._cached_manifest: list[ManifestEntry] = []
        # idx-assign region atomic; slow MinIO PUT outside the lock.
        self._add_lock = asyncio.Lock()
        self._live_last_flush = 0.0
        # Lazy artifact client — every tier flows through add_page.
        self._artifact_client: "httpx.AsyncClient | None" = None
        self._artifact_lock = asyncio.Lock()

    async def add_page(
        self,
        *,
        slug: str,
        url: str,
        body: str,
        tier: str,
        title: str = "",
    ) -> ManifestEntry:
        """Stream a page to MinIO + append the live manifest. Concurrent-safe:
        idx-assign + manifest append are locked, MinIO PUT is not.

        Normalized body → `ingestion/`; raw body → `ingestion-raw/` for
        reversibility across normalizer-version bumps.
        """
        # Markdown-side artifact hook for tiers 1/2/3/5 (tier4 uses HTML-stage).
        if url:
            try:
                client = await self._get_artifact_client()
                body, n_art = await extract_and_save_artifacts_from_md(
                    body, 
                    url, 
                    slug = self.framework_slug,
                    store = self, 
                    client = client,
                )
                if n_art:
                    logger.info(
                        f"[store] md-artifacts: {n_art} saved for "
                        f"slug={slug!r} url={url[:80]!r}"
                    )
            except Exception as e:
                logger.warning(
                    f"[store] md-artifact extraction failed for slug={slug!r}: "
                    f"{type(e).__name__}: {e}"
                )
        # Normalize before MinIO write; best-effort fall-through on bug.
        normalized_body = body
        try:
            normalized_body = normalize_doc(body).body
        except Exception as e:
            logger.warning(
                f"[store] normalize_doc failed for slug={slug!r}: "
                f"{type(e).__name__}: {e}; falling back to raw body"
            )
        normalized_bytes = len(normalized_body.encode("utf-8"))
        async with self._add_lock:
            idx = len(self._cached_manifest)
            key = page_key(self.framework_slug, idx, slug)
            entry = ManifestEntry(
                idx = idx, 
                slug = slug, 
                url = url, 
                tier = tier,
                bytes = normalized_bytes, 
                title = title or slug, 
                key = key,
            )
            self._cached_manifest.append(entry)
        await asyncio.gather(
            self.minio.write(key, normalized_body, content_type = "text/markdown"),
            self.minio.write(
                raw_page_key(self.framework_slug, idx, slug),
                body, content_type = "text/markdown",
            ),
        )
        # Vault sees the same bytes synth will see; best-effort.
        body = normalized_body
        try:
            await self._build_and_persist_vault(idx, slug, body)
        except Exception as e:
            logger.warning(
                f"[store] vault build failed for idx={idx} slug={slug!r}: "
                f"{type(e).__name__}: {e}"
            )
        await self._write_live_manifest()
        return entry

    async def _get_artifact_client(self) -> httpx.AsyncClient:
        """Lazy singleton httpx client for the markdown artifact extractor.
        One pool per Store so add_page calls reuse keepalive sockets."""
        if self._artifact_client is not None:
            return self._artifact_client
        async with self._artifact_lock:
            if self._artifact_client is not None:
                return self._artifact_client
            self._artifact_client = httpx.AsyncClient(
                timeout = httpx.Timeout(30.0, connect = 10.0),
                headers = {
                    "User-Agent": "COELHONexus-DocsDistiller-Artifacts/1.0",
                    "Accept": "image/*, video/*, audio/*, */*;q=0.5",
                },
                limits = httpx.Limits(
                    max_connections = 20, 
                    max_keepalive_connections = 10),
                follow_redirects = True,
            )
            return self._artifact_client

    def reorder_by_url_list(self, url_list: list[str]) -> None:
        """Reorder manifest entries to match `url_list` (the discovery order).

        Parallel fetch + idx-assign-by-completion would otherwise list pages
        in race order, not chapter order (Bash GNU manual was the regression).
        `entry.idx` stays unchanged — only the array order changes.
        """
        if not url_list or not self._cached_manifest:
            return
        url_pos = {u: i for i, u in enumerate(url_list)}
        sentinel = len(url_list)

        def _key(entry: ManifestEntry) -> tuple[int, int]:
            base = (entry.url or "").split("#", 1)[0]
            return (url_pos.get(base, sentinel), entry.idx)

        self._cached_manifest.sort(key = _key)

    async def close(self) -> None:
        """Release the lazy httpx artifact client. Idempotent."""
        if self._artifact_client is not None:
            try:
                await self._artifact_client.aclose()
            except Exception as e:
                logger.warning(f"[store] artifact client close failed: {e}")
            finally:
                self._artifact_client = None

    async def add_artifact(
        self, *, slug: str, name: str, data: bytes, content_type: str,
    ) -> str:
        """Persist a media artifact to `ingestion/{slug}/artifacts/{name}`.
        `name` is content-addressed (`{sha256[:16]}.{ext}`)."""
        if slug and slug != self.framework_slug:
            logger.warning(
                f"[store] add_artifact slug mismatch: got {slug!r}, "
                f"store is bound to {self.framework_slug!r}; using bound slug"
            )
        key = artifact_key(self.framework_slug, name)
        await self.minio.write(key, data, content_type = content_type)
        return key

    async def _build_and_persist_vault(
        self, idx: int, slug: str, body: str,
    ) -> None:
        """Sentinelize body + persist vault manifest + sentinelized text.
        Empty vaults still get written so synth has a uniform read path."""
        source_key = page_key(self.framework_slug, idx, slug)
        sentinelized, manifest = build_manifest(
            framework = self.framework_slug,
            source_key = source_key,
            md_text = body,
        )
        vk = vault_manifest_key(self.framework_slug, idx, slug)
        sk = vault_sentinelized_key(self.framework_slug, idx, slug)
        await asyncio.gather(
            self.minio.write(
                vk, 
                manifest.model_dump_json(), 
                content_type = "application/json"),
            self.minio.write(
                sk, 
                sentinelized, 
                content_type = "text/markdown"),
        )
        n = len(manifest.entries)
        if n:
            logger.info(
                f"[store] vault built idx={idx} slug={slug!r}: {n} fence(s) -> {vk}"
            )

    async def read_body(self, idx: int) -> str:
        if idx < 0 or idx >= len(self._cached_manifest):
            raise IndexError(
                f"idx {idx} out of range [0, {len(self._cached_manifest)})"
            )
        return await self.minio.read_text(self._cached_manifest[idx].key)

    async def delete_body(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._cached_manifest):
            return
        try:
            await self.minio.delete(self._cached_manifest[idx].key)
        except Exception as e:
            logger.info(f"[store] delete body idx={idx} skipped: {e}")

    async def replace_manifest(self, entries: list[ManifestEntry]) -> None:
        """Atomically replace the manifest. Caller writes new bodies first."""
        self._cached_manifest = list(entries)
        await self._write_live_manifest(force=True)

    async def write_body_by_key(self, key: str, body: str) -> int:
        return await self.minio.write(key, body, content_type="text/markdown")

    async def read_body_by_key(self, key: str) -> str:
        return await self.minio.read_text(key)

    async def delete_body_by_key(self, key: str) -> None:
        try:
            await self.minio.delete(key)
        except Exception as e:
            logger.info(f"[store] delete {key} skipped: {e}")

    async def finalize(self, extra: dict | None = None) -> None:
        """Write the canonical MinIO manifest (once per run, after post)."""
        payload = {
            "framework_slug": self.framework_slug,
            "ingested_at":    time.time(),
            "page_count":     len(self._cached_manifest),
            "total_bytes":    sum(e.bytes for e in self._cached_manifest),
            "entries":        [asdict(e) for e in self._cached_manifest],
        }
        if extra:
            payload.update(extra)
        try:
            await self.minio.write(
                manifest_key(self.framework_slug),
                json.dumps(payload, separators = (",", ":")),
                content_type = "application/json",
            )
        except Exception as e:
            logger.warning(f"[store] manifest write to MinIO failed: {e}")

    async def _write_live_manifest(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._live_last_flush) < LIVE_MANIFEST_THROTTLE_S:
            return
        self._live_last_flush = now
        try:
            await self.r.set(
                live_manifest_key(self.run_id),
                json.dumps([asdict(e) for e in self._cached_manifest]),
                ex = TTL_S,
            )
        except Exception as e:
            logger.warning(f"[store] live manifest write failed: {e}")

    @property
    def manifest(self) -> list[ManifestEntry]:
        return list(self._cached_manifest)

    @classmethod
    async def from_existing(
        cls,
        run_id: str,
        framework_slug: str,
        r: redis_aio.Redis,
        minio: MinIOStorage,
    ) -> "Store":
        """Construct a Store with the canonical manifest pre-loaded (debug)."""
        s = cls(run_id, framework_slug, r, minio)
        m = await read_framework_manifest(minio, framework_slug)
        if m:
            valid = {f.name for f in fields(ManifestEntry)}
            for e in m.get("entries", []):
                s._cached_manifest.append(ManifestEntry(
                    **{k: v for k, v in e.items() if k in valid}
                ))
        return s


async def read_live_manifest(r: redis_aio.Redis, run_id: str) -> list[dict]:
    """Manifest for an in-flight run (Redis), polled by /runs/{id}."""
    try:
        raw = await r.get(live_manifest_key(run_id))
    except Exception:
        return []
    if not raw:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return []


async def read_framework_manifest(
    minio: MinIOStorage, framework_slug: str,
) -> Optional[dict]:
    """Canonical per-framework manifest. None if no run finalized for slug."""
    try:
        raw = await minio.read_text(manifest_key(framework_slug))
    except Exception:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def read_framework_page(
    minio: MinIOStorage, framework_slug: str, idx: int,
) -> Optional[str]:
    """Resolve idx → MinIO key via the manifest, then read the body."""
    m = await read_framework_manifest(minio, framework_slug)
    if not m:
        return None
    entries = m.get("entries", [])
    if idx < 0 or idx >= len(entries):
        return None
    key = entries[idx].get("key") or page_key(
        framework_slug, idx, entries[idx].get("slug", ""),
    )
    try:
        return await minio.read_text(key)
    except Exception as e:
        logger.info(f"[store] page read failed (idx={idx}, key={key}): {e}")
        return None
