"""Per-framework MinIO snapshot/restore. Layout: `ingestion/{slug}/_snapshots/{ts}/`.

`ts` is `YYYYMMDDTHHMMSSZ` UTC so alphabetical = chronological. Debug router
only; not invoked by the ingestion path.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .keys import framework_prefix, snapshot_prefix
from .params import SNAPSHOTS_SUBDIR
from .service import MinIOStorage


logger = logging.getLogger(__name__)


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
    dst = snapshot_prefix(framework_slug, ts)
    t0 = time.monotonic()
    n = await minio.copy_prefix(
        src, dst, skip_substring = f"/{SNAPSHOTS_SUBDIR}",
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
    snaps_prefix = framework_prefix(framework_slug) + SNAPSHOTS_SUBDIR
    names = await minio.list_subfolders(snaps_prefix)
    return sorted(names, reverse = True)


async def restore(
    minio: MinIOStorage,
    framework_slug: str,
    ts: str,
) -> dict:
    """Overwrite canonical content with the snapshot. Deletes current
    canonical (preserving `_snapshots/`) then copies snapshot back."""
    src = snapshot_prefix(framework_slug, ts)
    snap_keys = await minio.list(src)
    if not snap_keys:
        raise ValueError(
            f"snapshot {ts!r} not found for {framework_slug!r}"
        )

    canonical_prefix = framework_prefix(framework_slug)
    cur = await minio.list(canonical_prefix)
    cur = [k for k in cur if f"/{SNAPSHOTS_SUBDIR}" not in k]
    deleted = 0
    if cur:
        sem = asyncio.BoundedSemaphore(32)
        async with minio._client() as s3:
            async def _one(k: str) -> None:
                async with sem:
                    await s3.delete_object(Bucket = minio.bucket, Key = k)
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
    prefix = snapshot_prefix(framework_slug, ts)
    n = await minio.delete_prefix(prefix)
    return {"framework_slug": framework_slug, "ts": ts, "deleted": n}
