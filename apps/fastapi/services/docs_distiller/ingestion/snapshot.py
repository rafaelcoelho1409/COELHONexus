"""Snapshot / restore for per-framework MinIO content.

Lets you freeze the current state of an ingested framework before tuning
a downstream step (post-process thresholds, manifest payload shape, etc.)
and roll back if the tweak makes things worse.

Layout under each framework:

    ingestion/{slug}/                     ← canonical (active)
    ingestion/{slug}/_snapshots/{ts}/     ← snapshots (one per take_snapshot call)

`ts` is `YYYYMMDDTHHMMSSZ` UTC so alphabetical listing equals chronological.

Used by the debug router; not invoked by the normal ingestion path.
"""
import logging
import time
from datetime import datetime, timezone

from .storage_minio import MinIOStorage, framework_prefix


logger = logging.getLogger(__name__)


_SNAPSHOTS_SUBDIR = "_snapshots/"


def _snapshot_prefix(framework_slug: str, ts: str) -> str:
    return f"{framework_prefix(framework_slug)}{_SNAPSHOTS_SUBDIR}{ts}/"


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def take(
    minio: MinIOStorage,
    framework_slug: str,
    *,
    label: str | None = None,
) -> dict:
    """Copy the current canonical content into `_snapshots/{ts}/`. Skips
    the `_snapshots/` subtree itself so we don't snapshot snapshots."""
    src = framework_prefix(framework_slug)
    ts = _now_ts() + (f"-{label}" if label else "")
    dst = _snapshot_prefix(framework_slug, ts)
    t0 = time.monotonic()
    n = await minio.copy_prefix(
        src, dst, skip_substring=f"/{_SNAPSHOTS_SUBDIR}",
    )
    dt_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"[snapshot] {framework_slug} → {ts}: {n} files in {dt_ms}ms"
    )
    return {
        "framework_slug": framework_slug, "ts": ts,
        "files_copied": n, "took_ms": dt_ms,
    }


async def list_snapshots(
    minio: MinIOStorage, framework_slug: str,
) -> list[str]:
    """Return snapshot timestamps for `slug`, newest first."""
    snaps_prefix = framework_prefix(framework_slug) + _SNAPSHOTS_SUBDIR
    names = await minio.list_subfolders(snaps_prefix)
    return sorted(names, reverse=True)


async def restore(
    minio: MinIOStorage,
    framework_slug: str,
    ts: str,
) -> dict:
    """Overwrite canonical content with the snapshot. Deletes current
    canonical (preserving `_snapshots/`) then copies snapshot back."""
    src = _snapshot_prefix(framework_slug, ts)
    snap_keys = await minio.list(src)
    if not snap_keys:
        raise ValueError(
            f"snapshot {ts!r} not found for {framework_slug!r}"
        )

    canonical_prefix = framework_prefix(framework_slug)
    # Delete current canonical content (excluding the snapshots tree).
    cur = await minio.list(canonical_prefix)
    cur = [k for k in cur if f"/{_SNAPSHOTS_SUBDIR}" not in k]
    deleted = 0
    if cur:
        import asyncio
        sem = asyncio.BoundedSemaphore(32)

        async def _one(k: str) -> None:
            async with sem:
                async with minio._client() as s3:
                    await s3.delete_object(Bucket=minio.bucket, Key=k)

        await asyncio.gather(*(_one(k) for k in cur))
        deleted = len(cur)

    t0 = time.monotonic()
    copied = await minio.copy_prefix(src, canonical_prefix)
    dt_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"[snapshot] restored {framework_slug} from {ts}: "
        f"-{deleted} +{copied} in {dt_ms}ms"
    )
    return {
        "framework_slug": framework_slug, "ts": ts,
        "deleted": deleted, "copied": copied, "took_ms": dt_ms,
    }


async def delete_snapshot(
    minio: MinIOStorage, framework_slug: str, ts: str,
) -> dict:
    prefix = _snapshot_prefix(framework_slug, ts)
    n = await minio.delete_prefix(prefix)
    return {"framework_slug": framework_slug, "ts": ts, "deleted": n}
