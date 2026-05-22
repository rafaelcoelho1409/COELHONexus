"""MinIO async storage adapter + per-framework manifest/body store.

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

Store layer:

  Redis  dd:runs:{run_id}:manifest                    — live snapshot while run is in flight
  MinIO  ingestion/{slug}/manifest.json               — canonical manifest (post-finalize)
  MinIO  ingestion/{slug}/pages/{idx:04d}-{slug}.md   — page bodies

Manifest in Redis is keyed by run_id so the live progress UI can poll one
specific in-flight ingestion. The canonical MinIO manifest is keyed by
framework_slug and is what the library view + cached-check read.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from typing import Optional

import aioboto3
import redis.asyncio as redis_aio
from botocore.config import Config
from botocore.exceptions import ClientError

from .constants import (
    _TTL_S,
    framework_prefix,
    live_manifest_key,
    manifest_key,
    page_key,
    raw_page_key,
    vault_manifest_key,
    vault_sentinelized_key,
)
from .types import ContentType, ManifestEntry


logger = logging.getLogger(__name__)


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

    async def read_bytes(self, key: str) -> bytes:
        """Raw binary read. Used for non-text artifacts (numpy .npz blobs
        in particular — embeddings, cluster matrices)."""
        async with self._client() as s3:
            resp = await s3.get_object(Bucket=self.bucket, Key=key)
            async with resp["Body"] as stream:
                return await stream.read()

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
        boto3 session per key -> 1000 deletes paid 1000 TCP handshakes;
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

    async def read_many(
        self,
        keys: list[str],
        max_concurrent: int = 16,
        chunk_size: int = 256,
        chunk_timeout_s: float = 60.0,
        max_chunk_retries: int = 3,
        encoding: str = "utf-8",
    ) -> list[str]:
        """Read many objects in parallel, in chunks. Same shape as `write_many`:
        one shared aioboto3 client per chunk (avoids per-key TLS+SigV4 cost),
        bounded concurrency inside each chunk, fresh client on retry, chunk
        timeout to escape stuck connections. Returns bodies in input order.

        Ported from v1 services/knowledge/storage.py `read_many`. Used by
        planner substeps (off_topic, dedup, map) that need to load page
        bodies on demand — corpus_load only carries keys in state so the
        Postgres checkpoint stays small."""
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
                        timeout=chunk_timeout_s,
                    )
                    results.extend(chunk_results)
                    break
                except (asyncio.TimeoutError, ClientError) as e:
                    last_err = e
                    if isinstance(e, ClientError):
                        code = (e.response or {}).get("Error", {}).get("Code", "")
                        if code not in (
                            "RequestTimeout", "InternalError",
                            "ServiceUnavailable", "SlowDown",
                        ):
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
                    resp = await s3.get_object(Bucket=self.bucket, Key=key)
                    async with resp["Body"] as stream:
                        data = await stream.read()
                return data.decode(encoding)
            return await asyncio.gather(*(_get_one(k) for k in chunk))


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


class Store:
    """Per-framework store; tagged with run_id for live-progress reads.

    Body writes always go to the canonical per-framework MinIO path so
    re-runs overwrite in place. The Redis manifest mirror is keyed by
    run_id (live view); the canonical MinIO manifest is written by
    `finalize()` at the end of dispatch."""

    def __init__(
        self,
        run_id: str,
        framework_slug: str,
        r: redis_aio.Redis,
        minio: MinIOStorage,
    ):
        self.run_id = run_id
        self.framework_slug = framework_slug
        self.r = r
        self.minio = minio
        self._cached_manifest: list[ManifestEntry] = []
        # Concurrency: tier 3/4a fetch many URLs in parallel and want to
        # stream each result to MinIO as soon as it arrives. The idx-assign
        # + manifest-append region needs to be atomic; the slow MinIO PUT
        # can happen outside the lock so we preserve real concurrency.
        import asyncio
        self._add_lock = asyncio.Lock()
        # Live-manifest write throttle. Without throttling, every add_page
        # serialises the FULL growing manifest to Redis — at page 1500 each
        # call writes a ~300KB blob; 1500 such writes compounded blew past
        # Celery's 30-min soft_time_limit on Docker's Tier 3 run. With 1s
        # throttling the live UI still polls smoothly (its own loop is 1.5s)
        # but worker CPU + Redis bandwidth stay bounded.
        self._live_last_flush = 0.0
        self._live_throttle_s = 1.0

    async def add_page(
        self,
        *,
        slug: str,
        url: str,
        body: str,
        tier: str,
        title: str = "",
    ) -> ManifestEntry:
        """Stream a fetched page to MinIO + append to the live manifest.
        Safe to call from many coroutines concurrently — the idx-assign +
        manifest-append region is locked; the MinIO PUT happens outside the
        lock so writes overlap freely.

        Normalization (added 2026-05-19, see SYNTH-ARCHITECTURE-SOTA doc):
        the body goes through `corpus_normalize.normalize_doc` BEFORE the
        canonical write so file viewer + embed_corpus + cluster + synth
        all see clean content. The raw body is preserved at
        `ingestion-raw/{slug}/pages/...` so backfills + normalizer
        version bumps stay reversible.
        """
        # Normalize before MinIO write. Best-effort: ingestion failures
        # MUST NOT cascade from a normalizer bug, so on exception we
        # fall through to the raw body.
        normalized_body = body
        try:
            from ...synth.corpus_normalize import (
                normalize_doc,
            )
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
                idx=idx, slug=slug, url=url, tier=tier,
                bytes=normalized_bytes, title=title or slug, key=key,
            )
            self._cached_manifest.append(entry)
        # MinIO PUT outside the lock — concurrent puts proceed in parallel.
        # Write normalized to the canonical path + raw to the parallel
        # `ingestion-raw/` prefix concurrently.
        import asyncio as _asyncio
        await _asyncio.gather(
            self.minio.write(key, normalized_body, content_type="text/markdown"),
            self.minio.write(
                raw_page_key(self.framework_slug, idx, slug),
                body, content_type="text/markdown",
            ),
        )
        # Replace `body` with normalized for downstream vault build —
        # vault must see the same bytes the LLM will see at synth time.
        body = normalized_body
        # Vault build — sentinelize code blocks for the synth pipeline.
        # Writes TWO sibling blobs under `synth-vault/{slug}/pages/...`
        # without touching the original `ingestion/{slug}/pages/...`
        # markdown the file viewer reads. Best-effort: ingestion is the
        # source of truth, so vault failures are logged but never crash
        # the run. See docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md step 5.
        try:
            await self._build_and_persist_vault(idx, slug, body)
        except Exception as e:
            logger.warning(
                f"[store] vault build failed for idx={idx} slug={slug!r}: "
                f"{type(e).__name__}: {e}"
            )
        await self._write_live_manifest()
        return entry

    async def _build_and_persist_vault(
        self, idx: int, slug: str, body: str,
    ) -> None:
        """Sentinelize a page's body + persist the vault manifest +
        sentinelized text to MinIO. Lazy-imports the vault module so
        the ingestion path doesn't pay a startup cost when not needed."""
        from ...synth.vault import build_manifest
        source_key = page_key(self.framework_slug, idx, slug)
        sentinelized, manifest = build_manifest(
            framework=self.framework_slug,
            source_key=source_key,
            md_text=body,
        )
        # Persist BOTH blobs concurrently — they're independent. Empty
        # vaults (docs with no fenced code) still get written so synth
        # has a uniform read path (no fallback-to-original logic).
        import asyncio as _asyncio
        vk = vault_manifest_key(self.framework_slug, idx, slug)
        sk = vault_sentinelized_key(self.framework_slug, idx, slug)
        await _asyncio.gather(
            self.minio.write(
                vk,
                manifest.model_dump_json(),
                content_type="application/json",
            ),
            self.minio.write(
                sk, sentinelized, content_type="text/markdown",
            ),
        )
        n = len(manifest.entries)
        if n:
            logger.info(
                f"[store] vault built idx={idx} slug={slug!r}: "
                f"{n} fence(s) -> {vk}"
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
        """Post-process rewrites the manifest. Caller must have already
        written each new entry's body to MinIO via `write_body_by_key`.
        force=True ensures the live manifest is up-to-date for any UI
        poll between post-process and finalize."""
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
        """Write the canonical MinIO manifest. Called once per run from
        `dispatch.run()` after post-process completes. `extra` lets the
        caller stash metadata like `ingested_at` / `framework_name` /
        `tier_kind` alongside the entries."""
        import time
        payload = {
            "framework_slug": self.framework_slug,
            "ingested_at": time.time(),
            "page_count": len(self._cached_manifest),
            "total_bytes": sum(e.bytes for e in self._cached_manifest),
            "entries": [asdict(e) for e in self._cached_manifest],
        }
        if extra:
            payload.update(extra)
        try:
            await self.minio.write(
                manifest_key(self.framework_slug),
                json.dumps(payload, separators=(",", ":")),
                content_type="application/json",
            )
        except Exception as e:
            logger.warning(f"[store] manifest write to MinIO failed: {e}")

    async def _write_live_manifest(self, force: bool = False) -> None:
        import time
        now = time.monotonic()
        if not force and (now - self._live_last_flush) < self._live_throttle_s:
            return
        self._live_last_flush = now
        try:
            await self.r.set(
                live_manifest_key(self.run_id),
                json.dumps([asdict(e) for e in self._cached_manifest]),
                ex=_TTL_S,
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
        """Construct a Store with the per-framework MinIO manifest
        pre-loaded. Used by debug endpoints that need to operate on
        previously-ingested content (re-run post-process, finalize, etc.)
        without re-downloading."""
        from dataclasses import fields
        s = cls(run_id, framework_slug, r, minio)
        m = await read_framework_manifest(minio, framework_slug)
        if m:
            valid = {f.name for f in fields(ManifestEntry)}
            for e in m.get("entries", []):
                s._cached_manifest.append(ManifestEntry(
                    **{k: v for k, v in e.items() if k in valid}
                ))
        return s


# =============================================================================
# Read-side helpers
# =============================================================================
async def read_live_manifest(
    r: redis_aio.Redis, run_id: str,
) -> list[dict]:
    """Manifest for an in-flight run (Redis). Used by /runs/{id} polling."""
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
    """Canonical per-framework manifest (MinIO). Returns None if no
    ingestion has been finalized for this slug yet."""
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
    """Resolve idx -> MinIO key via the manifest, then read the body."""
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
