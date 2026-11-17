"""Per-run immutable Store over the shared MinIO adapter; Redis is live state and the MinIO manifest is the publish pointer."""
from __future__ import annotations
import domains, infra
from . import domain, entities, keys, params

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


async def write_immutable(
    minio: infra.minio.service.MinIOStorage,
    key: str,
    content: str | bytes,
    content_type: str,
) -> None:
    """Write a versioned object once; retries may confirm identical bytes but cannot replace the captured input."""
    expected = content.encode("utf-8") if isinstance(content, str) else content
    if await minio.exists(key):
        if await minio.read_bytes(key) != expected:
            raise RuntimeError(f"immutable MinIO object conflict at {key}")
        return
    await minio.write(key, expected, content_type=content_type)


class Store:
    """Per-run store; raw and published pages are immutable, canonical manifest is switched last."""
    def __init__(
        self,
        run_id: str,
        framework_slug: str,
        r: infra.redis.service.Redis,
        minio: infra.minio.service.MinIOStorage,
        *,
        agent_framework_slug: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.framework_slug = framework_slug
        self.agent_framework_slug = agent_framework_slug or framework_slug
        self.r = r
        self.minio = minio
        self._cached_manifest: list[entities.ManifestEntry] = []
        self._manifest_lock = asyncio.Lock()
        self._live_write_lock = asyncio.Lock()
        self._selection_by_identity: dict[tuple[str, str, str], dict] = {}
        self._next_idx = 0
        # idx-assign region atomic; slow MinIO PUT outside the lock.
        self._add_lock = asyncio.Lock()
        self._live_last_flush = 0.0
        # Extraction dispatch may attach a non-blocking queue callback to start selection mid-run.
        self.on_page_added: Callable[[entities.ManifestEntry], None] | None = None
        # Tier 2 index summary (llms.txt blockquote) — set by tier2.service.run,
        # read by dispatch.finalize into the manifest. "" = absent (all tiers).
        self.index_summary: str = ""
        # The site's own index (llms.txt) as [(page URL, section heading), ...] in the order it lists them — set by Tier 1
        # for a bundle that is about to be split, read by post.service so the split pages follow the site's navigation
        # and carry its section headings. [] = none known.
        self.nav_index: list[tuple[str, str]] = []
        # Set by Tier 2 when it hands a site over to the sitemap although its index serves markdown pages (a curated index
        # that is a fraction of the docs): Tier 3 then asks for each page's markdown twin before scraping its HTML.
        self.markdown_twins: bool = False

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
        defer_vault: bool = False,
        relative_links: bool = True,
        keep_raw: bool = True,
    ) -> entities.ManifestEntry:
        """Store extracted Markdown unchanged; structural splitting happens after the tier finishes.

        Legacy keyword arguments remain accepted for tier compatibility. Vaults are built after the final
        extraction pages are published."""
        async with self._add_lock:
            idx = self._next_idx
            self._next_idx += 1
        raw_key = keys.raw_page_key(self.framework_slug, idx, slug, self.run_id, tier)
        await write_immutable(self.minio, raw_key, body, params.MARKDOWN_TYPE)

        async with self._manifest_lock:
            entry = domain.new_entry(
                self.framework_slug, idx, key=raw_key,
                slug = slug, url = url, tier = tier, size = len(body.encode("utf-8")),
                title = title, section = section, notes = notes, lastmod = lastmod, anchors = anchors,
                raw_key = raw_key,
            )
            self._cached_manifest.append(entry)
            self._cached_manifest.sort(key=lambda item: item.idx)
        if self.on_page_added is not None:
            self.on_page_added(entry)
        await self._write_live_manifest()
        return entry

    def reorder_by_url_list(self, url_list: list[str]) -> None:
        """Reorder manifest to discovery order; parallel fetch writes in race order, not chapter order (Bash GNU manual regression). entry.idx unchanged."""
        if not url_list or not self._cached_manifest:
            return
        self._cached_manifest = domain.ordered_by_urls(self._cached_manifest, url_list)

    async def close(self) -> None:
        """Compatibility hook; page storage no longer owns an HTTP client."""

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

    async def read_body(self, idx: int) -> str:
        if idx < 0 or idx >= len(self._cached_manifest):
            raise IndexError(
                f"idx {idx} out of range [0, {len(self._cached_manifest)})"
            )
        return await self.minio.read_text(self._cached_manifest[idx].key)

    async def replace_manifest(self, entries: list[entities.ManifestEntry]) -> None:
        """Atomically replace the manifest. Caller writes new bodies first."""
        async with self._manifest_lock:
            for current in self._cached_manifest:
                if current.page_selection:
                    self._selection_by_identity.setdefault(
                        domain.selection_identity(current),
                        dict(current.page_selection),
                    )
            for entry in entries:
                selection = self._selection_by_identity.get(domain.selection_identity(entry))
                if selection is not None:
                    entry.page_selection = dict(selection)
            self._cached_manifest = list(entries)
            self._next_idx = max((e.idx for e in entries), default=-1) + 1
        await self._write_live_manifest(force=True)

    async def read_body_by_key(self, key: str) -> str:
        return await self.minio.read_text(key)

    async def delete_body_by_key(self, key: str) -> None:
        try:
            await self.minio.delete(key)
        except Exception as e:
            logger.info(f"[store] delete {key} skipped: {e}")

    async def delete_vault(self, idx: int, slug: str) -> None:
        """Drop the vault pair of a page that post-processing is replacing. Vault keys carry the page's idx and slug,
        so once the page is rewritten under a new idx its old vault can never be found again — left alone it is
        orphaned storage (for a split bundle, ~10 MB)."""
        for key in (
            keys.vault_manifest_key(self.framework_slug, idx, slug),
            keys.vault_sentinelized_key(self.framework_slug, idx, slug),
        ):
            await self.delete_body_by_key(key)

    async def finalize(self, extra: dict | None = None) -> None:
        """Publish only after extracted pages and their code vaults are durable."""
        manifest_extra = dict(extra or {})
        is_agentic = bool(manifest_extra.get("agentic_run_id"))
        manifest_extra["page_content_mode"] = "agentic" if is_agentic else "extracted"
        if is_agentic:
            manifest_extra.setdefault("normalizer", "dd-ingestion-agentic-v1")
        payload = domain.manifest_payload(
            self.framework_slug,
            self._cached_manifest,
            ingested_at = time.time(),
            normalizer_version = 1 if is_agentic else 0,
            extra = manifest_extra,
        )
        try:
            await self.minio.write(
                keys.manifest_key(self.framework_slug),
                json.dumps(payload, separators = params.MANIFEST_JSON_SEPARATORS),
                content_type = params.JSON_TYPE,
            )
        except Exception:
            logger.exception("[store] canonical manifest publish failed")
            raise

    async def record_page_selections(
        self, selection_by_identity: dict[tuple[str, str, str], dict], *, force: bool = False,
    ) -> None:
        """Attach selector metadata by stable page identity and publish the live run snapshot."""
        if not selection_by_identity and not force:
            return
        async with self._manifest_lock:
            for identity, selection in selection_by_identity.items():
                self._selection_by_identity[identity] = dict(selection)
            for entry in self._cached_manifest:
                selection = self._selection_by_identity.get(domain.selection_identity(entry))
                if selection is not None:
                    entry.page_selection = dict(selection)
        await self._write_live_manifest(force=force)

    async def _write_live_manifest(self, force: bool = False) -> None:
        async with self._live_write_lock:
            now = time.monotonic()
            if not force and (now - self._live_last_flush) < params.LIVE_MANIFEST_THROTTLE_S:
                return
            self._live_last_flush = now
            async with self._manifest_lock:
                snapshot = domain.live_manifest_json(self._cached_manifest)
            try:
                await self.r.set(
                    keys.live_manifest_key(self.run_id),
                    snapshot,
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
    ) -> Store:
        """Construct a Store with the canonical manifest pre-loaded (debug)."""
        s = cls(run_id, framework_slug, r, minio)
        s._cached_manifest.extend(domain.entries_from_manifest(await read_framework_manifest(minio, framework_slug)))
        return s


async def read_live_manifest(r: infra.redis.service.Redis, run_id: str) -> list[dict]:
    """Manifest for an in-flight run (Redis), polled by /ingestion/runs/{id}."""
    try:
        raw = await r.get(keys.live_manifest_key(run_id))
    except Exception:
        return []
    return domain.parse_json(raw, [])


async def read_framework_manifest(
    minio: infra.minio.service.MinIOStorage, framework_slug: str,
) -> dict | None:
    """Canonical per-framework manifest. None if no run finalized for slug."""
    try:
        raw = await minio.read_text(keys.manifest_key(framework_slug))
    except Exception:
        return None
    return domain.parse_json(raw, None)


# Titles worked out for manifests written before pages were titled at ingestion, keyed by (framework, run). In
# memory only: reading a manifest never writes, and a re-ingestion writes real titles and gets a new run id.
_TITLE_CACHE: dict[tuple[str, str], dict[int, str]] = {}


async def with_display_titles(
    minio: infra.minio.service.MinIOStorage, framework_slug: str, manifest: dict,
) -> dict:
    """The manifest, with a real title on every page, and no two pages sharing one when their URLs can tell them apart.
    Real titles first (below), then `domain.with_unique_titles`."""
    return domain.with_unique_titles(await _with_real_titles(minio, framework_slug, manifest))


async def _with_real_titles(
    minio: infra.minio.service.MinIOStorage, framework_slug: str, manifest: dict,
) -> dict:
    """The manifest, with a real title on every page that is only named by its slug.

    Ingestions before 2026-10-26 titled every split page by its slug ("code-claude-com-llms-full-orchestrate-…"); the
    page's heading is in its body. Those pages are read once (bounded), their titles kept in memory, and handed out
    here so a library ingested earlier lists by name without being re-ingested. A page that cannot be read keeps what
    it had. Manifests that are already titled return untouched."""
    stale = [e for e in manifest.get("entries") or [] if domain.titled_by_slug(e)]
    if not stale:
        return manifest
    cache_key = domain.title_cache_key(framework_slug, manifest)
    titles = _TITLE_CACHE.get(cache_key)
    if titles is None:
        gate = asyncio.Semaphore(params.TITLE_READ_CONCURRENCY)

        async def one(entry: dict) -> tuple[int, str | None]:
            idx = int(entry.get("idx", 0))
            key = domain.entry_key(framework_slug, entry, idx)
            async with gate:
                try:
                    body = await minio.read_text(key)
                except Exception:  # noqa: BLE001 — a page that cannot be read keeps the title it had
                    return idx, None
            return idx, domains.dd.ingestion.post.domain.display_title(body, entry.get("slug", ""))

        titles = {idx: t for idx, t in await asyncio.gather(*(one(e) for e in stale)) if t}
        while len(_TITLE_CACHE) >= params.TITLE_CACHE_MAX:
            _TITLE_CACHE.pop(next(iter(_TITLE_CACHE)))
        _TITLE_CACHE[cache_key] = titles
    return domain.with_titles(manifest, titles)


async def read_framework_page(
    minio: infra.minio.service.MinIOStorage, framework_slug: str, idx: int,
) -> str | None:
    """Resolve idx → MinIO key via the manifest, then read the body."""
    m = await read_framework_manifest(minio, framework_slug)
    if not m:
        return None
    entry = domain.entry_with_idx(m.get("entries", []), idx)
    if entry is None:
        return None
    key = domain.entry_key(framework_slug, entry, idx)
    try:
        return await minio.read_text(key)
    except Exception as e:
        logger.info(f"[store] page read failed (idx={idx}, key={key}): {e}")
        return None


# --- MinIO snapshot/restore per framework (debug router only; not on the
# ingestion path). ts format YYYYMMDDTHHMMSSZ so alphabetical listing =
# chronological. ---

def _snapshot_now_ts() -> str:
    return datetime.now(UTC).strftime(params.SNAPSHOT_TS_FORMAT)


async def take_snapshot(
    minio: infra.minio.service.MinIOStorage,
    framework_slug: str,
    *,
    label: str | None = None,
) -> dict:
    """Copy the current canonical content into `_snapshots/{ts}/`. Skips
    the `_snapshots/` subtree itself so we don't snapshot snapshots."""
    src = keys.ingestion_prefix(framework_slug)
    ts = domain.snapshot_id(_snapshot_now_ts(), label)
    dst = keys.snapshot_prefix(framework_slug, ts)
    t0 = time.monotonic()
    n = await minio.copy_prefix(src, dst, skip_substring = params.SNAPSHOTS_SKIP_SUBSTRING)
    dt_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"[snapshot] {framework_slug} → {ts}: {n} files in {dt_ms}ms"
    )
    return domain.snapshot_taken(framework_slug, ts, n, dt_ms)


async def list_snapshots(
    minio: infra.minio.service.MinIOStorage, framework_slug: str,
) -> list[str]:
    """Return snapshot timestamps for `slug`, newest first."""
    snaps_prefix = keys.ingestion_prefix(framework_slug) + params.SNAPSHOTS_SUBDIR
    return domain.newest_first(await minio.list_subfolders(snaps_prefix))


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

    canonical_prefix = keys.ingestion_prefix(framework_slug)
    cur = domain.outside_snapshots(await minio.list(canonical_prefix))
    deleted = 0
    if cur:
        sem = asyncio.BoundedSemaphore(params.DELETE_CONCURRENCY)
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
    return domain.snapshot_restored(framework_slug, ts, deleted, copied, dt_ms)


async def delete_snapshot(
    minio: infra.minio.service.MinIOStorage, framework_slug: str, ts: str,
) -> dict:
    prefix = keys.snapshot_prefix(framework_slug, ts)
    n = await minio.delete_prefix(prefix)
    return domain.snapshot_deleted(framework_slug, ts, n)
