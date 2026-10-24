"""Per-framework Store over the shared MinIO adapter (`infra.minio`). Redis manifest keyed by run_id (live); MinIO manifest keyed by framework_slug (canonical on finalize)."""
from __future__ import annotations
import domains, infra
from . import entities, keys, params

import asyncio
import json
import logging
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from typing import Optional

import httpx


logger = logging.getLogger(__name__)


class Store:
    """Per-framework store; re-runs overwrite canonical MinIO path in place. Redis manifest keyed by run_id (live); MinIO manifest written on finalize (canonical)."""
    def __init__(
        self,
        run_id: str,
        framework_slug: str,
        r: infra.redis.service.Redis,
        minio: infra.minio.service.MinIOStorage,
    ) -> None:
        self.run_id = run_id
        self.framework_slug = framework_slug
        self.r = r
        self.minio = minio
        self._cached_manifest: list[entities.ManifestEntry] = []
        # idx-assign region atomic; slow MinIO PUT outside the lock.
        self._add_lock = asyncio.Lock()
        self._live_last_flush = 0.0
        # Lazy artifact client — every tier flows through add_page.
        self._artifact_client: "httpx.AsyncClient | None" = None
        self._artifact_lock = asyncio.Lock()
        # Tier 2 index summary (llms.txt blockquote) — set by tier2.service.run,
        # read by dispatch.finalize into the manifest. "" = absent (all tiers).
        self.index_summary: str = ""

    async def add_page(
        self,
        *,
        slug: str,
        url: str,
        body: str,
        tier: str,
        title: str = "",
        section: str = "",
        notes: str = "",
        lastmod: str = "",
        anchors: list | None = None,
    ) -> entities.ManifestEntry:
        """Stream page to store. idx-assign+manifest append are locked; MinIO PUT is not (concurrent writes to distinct keys). Raw body also → ingestion-raw/ for normalizer-version reversibility."""
        # Markdown-side artifact hook for tiers 1/2/3/5 (tier4 uses HTML-stage).
        if url:
            try:
                client = await self._get_artifact_client()
                body, n_art = await domains.dd.ingestion.artifacts.service.extract_and_save_artifacts_from_md(
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
            normalized_body = domains.dd.synth.nodes.corpus_normalize.domain.normalize_doc(body).body
        except Exception as e:
            logger.warning(
                f"[store] normalize_doc failed for slug={slug!r}: "
                f"{type(e).__name__}: {e}; falling back to raw body"
            )
        normalized_bytes = len(normalized_body.encode("utf-8"))
        async with self._add_lock:
            idx = len(self._cached_manifest)
            key = keys.page_key(self.framework_slug, idx, slug)
            entry = entities.ManifestEntry(
                idx = idx, 
                slug = slug, 
                url = url, 
                tier = tier,
                bytes = normalized_bytes, 
                title = title or slug, 
                key = key,
                section = section or "",
                notes = notes or "",
                lastmod = lastmod or "",
                anchors = list(anchors or []),
            )
            self._cached_manifest.append(entry)
        await asyncio.gather(
            self.minio.write(key, normalized_body, content_type = "text/markdown"),
            self.minio.write(
                keys.raw_page_key(self.framework_slug, idx, slug),
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
        """Reorder manifest to discovery order; parallel fetch writes in race order, not chapter order (Bash GNU manual regression). entry.idx unchanged."""
        if not url_list or not self._cached_manifest:
            return
        url_pos = {u: i for i, u in enumerate(url_list)}
        sentinel = len(url_list)

        def _key(entry: entities.ManifestEntry) -> tuple[int, int]:
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
        key = keys.artifact_key(self.framework_slug, name)
        await self.minio.write(key, data, content_type = content_type)
        return key

    async def _build_and_persist_vault(
        self, idx: int, slug: str, body: str,
    ) -> None:
        """Sentinelize body + persist vault manifest + sentinelized text.
        Empty vaults still get written so synth has a uniform read path."""
        source_key = keys.page_key(self.framework_slug, idx, slug)
        sentinelized, manifest = domains.dd.synth.nodes.vault.domain.build_manifest(
            framework = self.framework_slug,
            source_key = source_key,
            md_text = body,
        )
        vk = keys.vault_manifest_key(self.framework_slug, idx, slug)
        sk = keys.vault_sentinelized_key(self.framework_slug, idx, slug)
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

    async def replace_manifest(self, entries: list[entities.ManifestEntry]) -> None:
        """Atomically replace the manifest. Caller writes new bodies first."""
        self._cached_manifest = list(entries)
        await self._write_live_manifest(force=True)

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
            # Pages above were written through add_page's normalize_doc at this
            # version; Synth's ensure_framework_normalized compares against it.
            "normalizer_version": domains.dd.synth.nodes.corpus_normalize.versions.NORMALIZER_VERSION,
        }
        if extra:
            payload.update(extra)
        try:
            await self.minio.write(
                keys.manifest_key(self.framework_slug),
                json.dumps(payload, separators = (",", ":")),
                content_type = "application/json",
            )
        except Exception as e:
            logger.warning(f"[store] manifest write to MinIO failed: {e}")

    async def _write_live_manifest(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._live_last_flush) < params.LIVE_MANIFEST_THROTTLE_S:
            return
        self._live_last_flush = now
        try:
            await self.r.set(
                keys.live_manifest_key(self.run_id),
                json.dumps([asdict(e) for e in self._cached_manifest]),
                ex = params.TTL_S,
            )
        except Exception as e:
            logger.warning(f"[store] live manifest write failed: {e}")

    @property
    def manifest(self) -> list[entities.ManifestEntry]:
        return list(self._cached_manifest)

    @classmethod
    async def from_existing(
        cls,
        run_id: str,
        framework_slug: str,
        r: infra.redis.service.Redis,
        minio: infra.minio.service.MinIOStorage,
    ) -> "Store":
        """Construct a Store with the canonical manifest pre-loaded (debug)."""
        s = cls(run_id, framework_slug, r, minio)
        m = await read_framework_manifest(minio, framework_slug)
        if m:
            valid = {f.name for f in fields(entities.ManifestEntry)}
            for e in m.get("entries", []):
                s._cached_manifest.append(entities.ManifestEntry(
                    **{k: v for k, v in e.items() if k in valid}
                ))
        return s


async def read_live_manifest(r: infra.redis.service.Redis, run_id: str) -> list[dict]:
    """Manifest for an in-flight run (Redis), polled by /runs/{id}."""
    try:
        raw = await r.get(keys.live_manifest_key(run_id))
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
    minio: infra.minio.service.MinIOStorage, framework_slug: str,
) -> Optional[dict]:
    """Canonical per-framework manifest. None if no run finalized for slug."""
    try:
        raw = await minio.read_text(keys.manifest_key(framework_slug))
    except Exception:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def read_framework_page(
    minio: infra.minio.service.MinIOStorage, framework_slug: str, idx: int,
) -> Optional[str]:
    """Resolve idx → MinIO key via the manifest, then read the body."""
    m = await read_framework_manifest(minio, framework_slug)
    if not m:
        return None
    entries = m.get("entries", [])
    if idx < 0 or idx >= len(entries):
        return None
    key = entries[idx].get("key") or keys.page_key(
        framework_slug, idx, entries[idx].get("slug", ""),
    )
    try:
        return await minio.read_text(key)
    except Exception as e:
        logger.info(f"[store] page read failed (idx={idx}, key={key}): {e}")
        return None


# --- MinIO snapshot/restore per framework (debug router only; not on the
# ingestion path). ts format YYYYMMDDTHHMMSSZ so alphabetical listing =
# chronological. ---

def _snapshot_now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def take_snapshot(
    minio: infra.minio.service.MinIOStorage,
    framework_slug: str,
    *,
    label: str | None = None,
) -> dict:
    """Copy the current canonical content into `_snapshots/{ts}/`. Skips
    the `_snapshots/` subtree itself so we don't snapshot snapshots."""
    src = keys.framework_prefix(framework_slug)
    ts = _snapshot_now_ts() + (f"-{label}" if label else "")
    dst = keys.snapshot_prefix(framework_slug, ts)
    t0 = time.monotonic()
    n = await minio.copy_prefix(
        src, dst, skip_substring = f"/{params.SNAPSHOTS_SUBDIR}",
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
    minio: infra.minio.service.MinIOStorage, framework_slug: str,
) -> list[str]:
    """Return snapshot timestamps for `slug`, newest first."""
    snaps_prefix = keys.framework_prefix(framework_slug) + params.SNAPSHOTS_SUBDIR
    names = await minio.list_subfolders(snaps_prefix)
    return sorted(names, reverse = True)


async def restore_snapshot(
    minio: infra.minio.service.MinIOStorage,
    framework_slug: str,
    ts: str,
) -> dict:
    """Overwrite canonical content with the snapshot. Deletes current
    canonical (preserving `_snapshots/`) then copies snapshot back."""
    src = keys.snapshot_prefix(framework_slug, ts)
    snap_keys = await minio.list(src)
    if not snap_keys:
        raise ValueError(
            f"snapshot {ts!r} not found for {framework_slug!r}"
        )

    canonical_prefix = keys.framework_prefix(framework_slug)
    cur = await minio.list(canonical_prefix)
    cur = [k for k in cur if f"/{params.SNAPSHOTS_SUBDIR}" not in k]
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
    minio: infra.minio.service.MinIOStorage, framework_slug: str, ts: str,
) -> dict:
    prefix = keys.snapshot_prefix(framework_slug, ts)
    n = await minio.delete_prefix(prefix)
    return {"framework_slug": framework_slug, "ts": ts, "deleted": n}
