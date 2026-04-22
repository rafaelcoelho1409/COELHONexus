"""
Knowledge Distiller — MinIO Storage Service

All KD artifacts live in MinIO (the COELHOCloud object store, S3-compatible).
No local files — the whole pipeline reads and writes via this service.

LAYOUT (single bucket, prefix-namespaced):
    coelhonexus/
      {user_id}/
        knowledge/
          {framework}-{version}-{ts}/
            research/
              manifest.json
              plan.json
              raw/
                {slug}.md
            chapter01/
              README.md
              challenges.md
              flashcards.json
            chapter02/ ...
            summary.md
            DEBT.md
            exports/
              study.pdf, study.html, study.apkg

CLIENT: aioboto3 (async wrapper around aiobotocore + boto3). MinIO is
S3-compatible, so we point boto3 at MinIO via endpoint_url and use
signature_version=s3v4. All operations are async and yield to the event
loop — safe to call from FastAPI handlers and Celery async tasks.

SELF-PROVISIONING: ensure_bucket() is called once at app startup
(lifespan). It's idempotent — mirrors the _ensure_postgres_database()
pattern already in app.py. No out-of-band infra work needed.

CONCURRENCY: each operation opens its own aioboto3 client context. The
Session is shared (thread-safe, task-safe). For KD's load (~hundreds of
S3 ops per study run) this is more than fast enough; pool if we ever
hit bottlenecks.
"""
import asyncio
import logging
from typing import Literal
import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


ContentType = Literal[
    "text/markdown",
    "application/json",
    "text/plain",
    "text/html",
    "application/xml",
    "application/pdf",
    "application/epub+zip",
    "application/octet-stream",
]


class MinIOStudyStorage:
    """
    Async MinIO-backed storage for KD artifacts.

    Usage (one instance, held on app.state.study_storage):
        storage = MinIOStudyStorage(
            bucket = "coelhonexus",
            endpoint_url = os.environ["MINIO_ENDPOINT"],
            access_key = os.environ["AWS_ACCESS_KEY_ID"],
            secret_key = os.environ["AWS_SECRET_ACCESS_KEY"],
        )
        await storage.ensure_bucket()       # call once in lifespan
        await storage.write("foo/bar.md", "hello")
        data = await storage.read_text("foo/bar.md")
        keys = await storage.list("foo/")
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1"):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        # Session is safe to share across tasks; clients are opened per-operation.
        self._session = aioboto3.Session()
        # signature_version=s3v4 is REQUIRED for MinIO (default v2 won't work).
        # max_pool_connections bumped from botocore's default 10 so the
        # concurrent `put_object` calls in `write_many` have pool headroom.
        # connect_timeout and read_timeout are CRITICAL: without them,
        # aioboto3/aiobotocore-backed put_object calls can hang silently at
        # the aiohttp layer under concurrent load (documented class of bug,
        # aio-libs/aiobotocore#738 and #451, closed without upstream fix).
        # 10s connect / 30s read is generous for the 2-10 KB sections we
        # write while catching any socket-level stalls quickly.
        self._boto_config = Config(
            signature_version = "s3v4",
            max_pool_connections = 32,
            connect_timeout = 10,
            read_timeout = 30,
            # Built-in retry for the occasional transient 5xx / timeout.
            # Bumped from 3 to 10 per the community recommendation for
            # 16+ concurrent requests (Stitching this with the chunk-level
            # retry in write_many/read_many gives belt-and-suspenders recovery:
            # botocore retries transient HTTP errors on a live session;
            # write_many retries chunks with a FRESH session when a
            # whole-chunk timeout fires, covering the silent-hang case
            # that botocore's timer-based retries can't catch.)
            retries = {"max_attempts": 10, "mode": "standard"},
        )

    def _client(self):
        """Open an aioboto3 S3 client context manager. Always use as `async with`."""
        return self._session.client(
            "s3",
            endpoint_url = self.endpoint_url,
            aws_access_key_id = self._access_key,
            aws_secret_access_key = self._secret_key,
            region_name = self._region,
            config = self._boto_config,
        )

    # -------------------------------------------------------------------------
    # Bucket provisioning
    # -------------------------------------------------------------------------
    async def ensure_bucket(self) -> None:
        """
        Create the bucket if it doesn't exist. Idempotent — safe to call every
        startup. Mirrors _ensure_postgres_database() in app.py.
        """
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket = self.bucket)
                logger.info(f"MinIO bucket '{self.bucket}' already exists.")
                print(f"MinIO bucket '{self.bucket}' already exists.", flush = True)
                return
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                # 404 on head_bucket means doesn't exist; other codes are real errors
                if code not in ("404", "NoSuchBucket", "NoSuchKey"):
                    raise
            await s3.create_bucket(Bucket = self.bucket)
            logger.info(f"MinIO bucket '{self.bucket}' created.")
            print(f"MinIO bucket '{self.bucket}' created.", flush = True)

    # -------------------------------------------------------------------------
    # Primitive ops — keys are full object keys (not relative)
    # -------------------------------------------------------------------------
    async def write(
        self,
        key: str,
        content: str | bytes,
        content_type: ContentType = "text/markdown") -> int:
        """
        Write content to the given key. Strings are UTF-8 encoded.
        Returns the number of bytes written (useful for manifest entries).

        Retries on transient `IncompleteBody` ClientError — an aioboto3-level
        race surfaced under high write concurrency. Observed 2026-04-22 in
        the Tier 1 planner splitter running ~3700 parallel put_objects.
        Three attempts with brief exponential backoff (0.3s / 0.9s) — enough
        to ride out the race without amplifying actual outages.
        """
        body = content.encode("utf-8") if isinstance(content, str) else content
        last_err: Exception | None = None
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
                # IncompleteBody, RequestTimeout, and 5xx are the transient
                # classes worth retrying at the body-upload layer. 4xx other
                # than those (e.g. AccessDenied, NoSuchBucket) are permanent.
                transient = code in (
                    "IncompleteBody",
                    "RequestTimeout",
                    "InternalError",
                    "ServiceUnavailable",
                    "SlowDown",
                )
                if not transient or attempt == 2:
                    raise
                last_err = e
                # Backoff: 0.3s, 0.9s
                await asyncio.sleep(0.3 * (3 ** attempt))
                logger.info(
                    f"[storage] write {key!r} transient {code} (attempt {attempt+1}/3); retrying"
                )
        # Defensive — loop always either returns or raises above.
        if last_err is not None:
            raise last_err
        return len(body)

    async def read(self, key: str) -> bytes:
        """Read raw bytes of the object at `key`."""
        async with self._client() as s3:
            resp = await s3.get_object(Bucket = self.bucket, Key = key)
            async with resp["Body"] as stream:
                return await stream.read()

    async def read_text(self, key: str, encoding: str = "utf-8") -> str:
        """Read object and decode as text (UTF-8 default)."""
        data = await self.read(key)
        return data.decode(encoding)

    # -------------------------------------------------------------------------
    # Batched ops — share ONE aioboto3 client across many requests
    # -------------------------------------------------------------------------
    # Motivating measurement (2026-04-22): the Tier 1 planner splitter writes
    # thousands of small section files to MinIO. Using `write()` in a loop
    # (each call `async with self._client()`) paid a fresh TLS + SigV4 auth
    # handshake PER PUT. At Tailscale latency ~250 ms/handshake, the splitter
    # throughput collapsed to < 1 file/sec despite Semaphore(8) — the
    # handshakes serialized through the semaphore slot. One shared client
    # reuses a keep-alive connection pool for the whole batch, eliminating
    # that per-call overhead and letting the semaphore's 8 slots actually
    # run in parallel on top of an already-warm pool.
    async def write_many(
        self,
        items: "list[tuple[str, str | bytes, ContentType]]",
        max_concurrent: int = 16,
        chunk_size: int = 256,
        chunk_timeout_s: float = 60.0,
        max_chunk_retries: int = 3) -> list[int]:
        """
        Write many objects in parallel, in chunks, with:
          - A fresh aioboto3 client per chunk attempt (isolates session state).
          - `asyncio.wait_for` around each chunk to catch silent hangs that
            botocore's retry layer can't detect (aio-libs/aiobotocore#738).
          - Chunk-level retry-with-fresh-client when a chunk times out or
            returns a transient ClientError. The per-chunk timeout is the
            only escape hatch from a stalled aiohttp socket that never
            receives a response.
          - `BoundedSemaphore` limit tied to the botocore pool size (the
            pattern recommended by aiobotocore community discussions for
            avoiding pool starvation under concurrent put_object load).

        Returns bytes-written counts in input order.

        Parameters
        ----------
        max_concurrent   : in-flight PUTs per chunk (cap at max_pool_connections)
        chunk_size       : items per chunk (one fresh client per chunk attempt)
        chunk_timeout_s  : seconds allowed per chunk attempt before escalating
                           to a fresh-client retry. 60s covers the slowest
                           observed healthy chunk (20s) with 3x headroom.
        max_chunk_retries: attempts per chunk; 3 = 1 try + 2 retries with
                           fresh client, 1s/2s backoff between.
        """
        if not items:
            return []
        results: list[int] = []
        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            chunk_end = start + len(chunk)
            last_err: Exception | None = None
            for attempt in range(max_chunk_retries):
                try:
                    chunk_results = await asyncio.wait_for(
                        self._write_chunk(chunk, max_concurrent),
                        timeout = chunk_timeout_s,
                    )
                    results.extend(chunk_results)
                    break   # chunk succeeded
                except asyncio.TimeoutError as e:
                    last_err = e
                    logger.warning(
                        f"[storage] write_many chunk [{start}:{chunk_end}) "
                        f"attempt {attempt+1}/{max_chunk_retries} TIMED OUT "
                        f"after {chunk_timeout_s}s — retrying with fresh client"
                    )
                except ClientError as e:
                    code = (e.response or {}).get("Error", {}).get("Code", "")
                    transient = code in (
                        "IncompleteBody", "RequestTimeout", "InternalError",
                        "ServiceUnavailable", "SlowDown",
                    )
                    if not transient:
                        raise
                    last_err = e
                    logger.warning(
                        f"[storage] write_many chunk [{start}:{chunk_end}) "
                        f"attempt {attempt+1}/{max_chunk_retries} transient "
                        f"{code} — retrying with fresh client"
                    )
                if attempt < max_chunk_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))   # 1s, 2s
            else:
                # All attempts exhausted
                raise RuntimeError(
                    f"write_many chunk [{start}:{chunk_end}) failed after "
                    f"{max_chunk_retries} attempts; last error: "
                    f"{type(last_err).__name__}: {last_err}"
                )
        return results

    async def _write_chunk(
        self,
        chunk: "list[tuple[str, str | bytes, ContentType]]",
        max_concurrent: int) -> list[int]:
        """
        Write one chunk through a single fresh aioboto3 client. Raised
        exceptions and timeouts are handled by the caller (write_many).
        """
        sem = asyncio.BoundedSemaphore(max_concurrent)
        async with self._client() as s3:
            async def _put_one(
                key: str, content: str | bytes, content_type: ContentType,
            ) -> int:
                body = content.encode("utf-8") if isinstance(content, str) else content
                async with sem:
                    await s3.put_object(
                        Bucket = self.bucket,
                        Key = key,
                        Body = body,
                        ContentType = content_type,
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
        encoding: str = "utf-8") -> list[str]:
        """
        Read many objects in parallel, in chunks. Same chunk / fresh-client /
        timeout / retry strategy as `write_many` — see that method's
        docstring for the rationale.
        """
        if not keys:
            return []
        results: list[str] = []
        for start in range(0, len(keys), chunk_size):
            chunk = keys[start : start + chunk_size]
            chunk_end = start + len(chunk)
            last_err: Exception | None = None
            for attempt in range(max_chunk_retries):
                try:
                    chunk_results = await asyncio.wait_for(
                        self._read_chunk(chunk, max_concurrent, encoding),
                        timeout = chunk_timeout_s,
                    )
                    results.extend(chunk_results)
                    break
                except asyncio.TimeoutError as e:
                    last_err = e
                    logger.warning(
                        f"[storage] read_many chunk [{start}:{chunk_end}) "
                        f"attempt {attempt+1}/{max_chunk_retries} TIMED OUT "
                        f"after {chunk_timeout_s}s — retrying with fresh client"
                    )
                except ClientError as e:
                    code = (e.response or {}).get("Error", {}).get("Code", "")
                    transient = code in (
                        "RequestTimeout", "InternalError",
                        "ServiceUnavailable", "SlowDown",
                    )
                    if not transient:
                        raise
                    last_err = e
                    logger.warning(
                        f"[storage] read_many chunk [{start}:{chunk_end}) "
                        f"attempt {attempt+1}/{max_chunk_retries} transient "
                        f"{code} — retrying with fresh client"
                    )
                if attempt < max_chunk_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
            else:
                raise RuntimeError(
                    f"read_many chunk [{start}:{chunk_end}) failed after "
                    f"{max_chunk_retries} attempts; last error: "
                    f"{type(last_err).__name__}: {last_err}"
                )
        return results

    async def _read_chunk(
        self,
        chunk: list[str],
        max_concurrent: int,
        encoding: str) -> list[str]:
        """One chunk through a fresh aioboto3 client — see write_many above."""
        sem = asyncio.BoundedSemaphore(max_concurrent)
        async with self._client() as s3:
            async def _get_one(key: str) -> str:
                async with sem:
                    resp = await s3.get_object(Bucket = self.bucket, Key = key)
                    async with resp["Body"] as stream:
                        data = await stream.read()
                return data.decode(encoding)
            return await asyncio.gather(*(_get_one(k) for k in chunk))

    async def list(self, prefix: str) -> list[str]:
        """
        List all object keys under `prefix` (recursive; no delimiter).
        Returns the full keys (include the prefix). Empty list on no matches.
        """
        keys: list[str] = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket = self.bucket, Prefix = prefix):
                for obj in page.get("Contents", []) or []:
                    keys.append(obj["Key"])
        return keys

    async def delete(self, key: str) -> None:
        """Delete a single object. Idempotent — no error if the key is absent."""
        async with self._client() as s3:
            await s3.delete_object(Bucket = self.bucket, Key = key)

    async def exists(self, key: str) -> bool:
        """True if object exists at `key`."""
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket = self.bucket, Key = key)
                return True
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchKey"):
                    return False
                raise

    async def copy(self, src_key: str, dst_key: str) -> int:
        """
        Server-side copy from `src_key` to `dst_key` within the same bucket.
        One round-trip — MinIO copies the bytes internally, no client-side
        download/upload. Orders of magnitude faster than read→write for
        bulk restore (per-object ~1 RTT header exchange vs 2 RTTs + body
        transfer in each direction).

        Returns the copied object's content length. If the source doesn't
        exist, raises ClientError with code 404.
        """
        copy_source = {"Bucket": self.bucket, "Key": src_key}
        async with self._client() as s3:
            await s3.copy_object(
                Bucket = self.bucket,
                Key = dst_key,
                CopySource = copy_source,
            )
            # Return byte count for the copied object (used by manifest entries).
            head = await s3.head_object(Bucket = self.bucket, Key = dst_key)
            return int(head.get("ContentLength") or 0)
