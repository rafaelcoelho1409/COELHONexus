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
        self._boto_config = Config(signature_version = "s3v4")

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
        """
        body = content.encode("utf-8") if isinstance(content, str) else content
        async with self._client() as s3:
            await s3.put_object(
                Bucket = self.bucket,
                Key = key,
                Body = body,
                ContentType = content_type,
            )
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
