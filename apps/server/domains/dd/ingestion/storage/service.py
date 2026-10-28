"""Per-framework Store over the shared MinIO adapter (`infra.minio`). Redis manifest keyed by run_id (live); MinIO manifest keyed by framework_slug (canonical on finalize)."""
from __future__ import annotations
import domains, infra
from . import domain, entities, keys, params

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

import httpx


logger = logging.getLogger(__name__)


async def _cpu_bound(fn, *args, size: int, **kwargs):
    if size < params.CPU_THREAD_THRESHOLD_CHARS:
        return fn(*args, **kwargs)
    return await asyncio.to_thread(fn, *args, **kwargs)


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
        self._artifact_client: httpx.AsyncClient | None = None
        self._artifact_lock = asyncio.Lock()
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
    ) -> entities.ManifestEntry:
        """Stream page to store. idx-assign+manifest append are locked; MinIO PUT is not (concurrent writes to distinct keys). Raw body also → ingestion-raw/ for normalizer-version reversibility.

        `defer_vault`: skip the page's vault now. For a bundle that is about to be split into pages: its own vault would
        be built (seconds, ~10 MB) and then orphaned, while the dispatcher's vault backfill builds one per final page
        (and skips any page that already has one), so nothing is lost.

        The stored copy has its site-relative links made absolute against `url`. `relative_links=False` limits that to
        `/x` links (a bundle's `./x` is relative to each section's own URL, which the split knows and `url` does not)."""
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
            normalized_body = (
                await _cpu_bound(domains.dd.synth.nodes.corpus_normalize.domain.normalize_doc, body, size = len(body))
            ).body
        except Exception as e:
            logger.warning(
                f"[store] normalize_doc failed for slug={slug!r}: "
                f"{type(e).__name__}: {e}; falling back to raw body"
            )
        if url:
            normalized_body = await _cpu_bound(
                domains.dd.ingestion.post.domain.absolutize_links, normalized_body, url,
                size = len(normalized_body), relative = relative_links,
            )
        normalized_bytes = len(normalized_body.encode("utf-8"))
        async with self._add_lock:
            idx = len(self._cached_manifest)
            entry = domain.new_entry(
                self.framework_slug, idx,
                slug = slug, url = url, tier = tier, size = normalized_bytes,
                title = title, section = section, notes = notes, lastmod = lastmod, anchors = anchors,
            )
            self._cached_manifest.append(entry)
        await asyncio.gather(
            self.minio.write(entry.key, normalized_body, content_type = params.MARKDOWN_TYPE),
            self.minio.write(
                keys.raw_page_key(self.framework_slug, idx, slug),
                body, content_type = params.MARKDOWN_TYPE,
            ),
        )
        # Vault sees the same bytes synth will see; best-effort.
        body = normalized_body
        if not defer_vault:
            try:
                await self._build_and_persist_vault(idx, slug, body)
            except Exception as e:  # noqa: BLE001 — the vault is best-effort; the page is already stored
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
                timeout = httpx.Timeout(params.ARTIFACT_TIMEOUT_S, connect = params.ARTIFACT_CONNECT_TIMEOUT_S),
                headers = {
                    "User-Agent": params.ARTIFACT_USER_AGENT,
                    "Accept": params.ARTIFACT_ACCEPT,
                },
                limits = httpx.Limits(
                    max_connections = params.ARTIFACT_MAX_CONNECTIONS,
                    max_keepalive_connections = params.ARTIFACT_MAX_KEEPALIVE),
                follow_redirects = True,
            )
            return self._artifact_client

    def reorder_by_url_list(self, url_list: list[str]) -> None:
        """Reorder manifest to discovery order; parallel fetch writes in race order, not chapter order (Bash GNU manual regression). entry.idx unchanged."""
        if not url_list or not self._cached_manifest:
            return
        self._cached_manifest = domain.ordered_by_urls(self._cached_manifest, url_list)

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
        sentinelized, manifest = await _cpu_bound(
            domains.dd.synth.nodes.vault.domain.build_manifest,
            framework = self.framework_slug,
            source_key = source_key,
            md_text = body,
            size = len(body),
        )
        vk = keys.vault_manifest_key(self.framework_slug, idx, slug)
        sk = keys.vault_sentinelized_key(self.framework_slug, idx, slug)
        await asyncio.gather(
            self.minio.write(
                vk,
                manifest.model_dump_json(),
                content_type = params.JSON_TYPE),
            self.minio.write(
                sk,
                sentinelized,
                content_type = params.MARKDOWN_TYPE),
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
        """Write the canonical MinIO manifest (once per run, after post)."""
        payload = domain.manifest_payload(
            self.framework_slug,
            self._cached_manifest,
            ingested_at = time.time(),
            normalizer_version = domains.dd.synth.nodes.corpus_normalize.versions.NORMALIZER_VERSION,
            extra = extra,
        )
        try:
            await self.minio.write(
                keys.manifest_key(self.framework_slug),
                json.dumps(payload, separators = params.MANIFEST_JSON_SEPARATORS),
                content_type = params.JSON_TYPE,
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
                domain.live_manifest_json(self._cached_manifest),
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
    """Manifest for an in-flight run (Redis), polled by /runs/{id}."""
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
    src = keys.framework_prefix(framework_slug)
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
    snaps_prefix = keys.framework_prefix(framework_slug) + params.SNAPSHOTS_SUBDIR
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

    canonical_prefix = keys.framework_prefix(framework_slug)
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


# --- The copy a re-ingestion replaces, kept until the new one is done. A refresh used to wipe the old copy first, so a
# failed or cancelled run left nothing (Diffusers was a 404 for 18 minutes; a failure would have been permanent). ---

async def stash_old_copy(minio: infra.minio.service.MinIOStorage, framework_slug: str) -> str | None:
    """Make room for a re-ingestion of `framework_slug`: copy the finalized copy aside, then empty its three prefixes. Returns
    the backup's id (what `restore_old_copy` / `drop_old_copy` take), or None when there was nothing worth keeping.
    A copy that is not finalized (a run that crashed) is not backed up over a backup an earlier run left: that backup is
    kept and returned instead."""
    older = domain.newest_first(await minio.list_subfolders(keys.backup_root(framework_slug)))
    backup_id: str | None = older[0] if older else None
    if await read_framework_manifest(minio, framework_slug) is not None:
        backup_id = _snapshot_now_ts()
        base = keys.backup_prefix(framework_slug, backup_id)
        for part, prefix in keys.framework_parts(framework_slug):
            await minio.copy_prefix(prefix, f"{base}{part}/", skip_substring = params.SNAPSHOTS_SKIP_SUBSTRING)
        for stale in older:
            if stale != backup_id:  # a backup made in the same second as an older one shares its id: it is not the stale one
                await minio.delete_prefix(keys.backup_prefix(framework_slug, stale))
    for _part, prefix in keys.framework_parts(framework_slug):
        await minio.delete_prefix(prefix)
    return backup_id


async def restore_old_copy(minio: infra.minio.service.MinIOStorage, framework_slug: str, backup_id: str) -> int:
    """Put a backup back as the framework's copy, in place of whatever a failed or cancelled run left. Returns the objects
    restored (0 when the backup is gone, in which case nothing is touched)."""
    base = keys.backup_prefix(framework_slug, backup_id)
    if not await minio.list(base):
        return 0
    restored = 0
    for part, prefix in keys.framework_parts(framework_slug):
        await minio.delete_prefix(prefix)
        restored += await minio.copy_prefix(f"{base}{part}/", prefix)
    return restored


async def latest_backup(minio: infra.minio.service.MinIOStorage, framework_slug: str) -> str | None:
    """The newest backup of `framework_slug`'s previous copy, if one is waiting (a run in flight, or one that crashed)."""
    found = domain.newest_first(await minio.list_subfolders(keys.backup_root(framework_slug)))
    return found[0] if found else None


async def drop_old_copy(minio: infra.minio.service.MinIOStorage, framework_slug: str, backup_id: str | None = None) -> int:
    """The new copy is finished: the backup of the old one is not needed. Without an id, every backup of the framework goes."""
    prefix = keys.backup_prefix(framework_slug, backup_id) if backup_id else keys.backup_root(framework_slug)
    return await minio.delete_prefix(prefix)
