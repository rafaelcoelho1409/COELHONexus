"""Per-framework manifest + body store.

Centralized layout: every ingestion of `framework_slug` shares one
canonical path in MinIO so future per-experience-level synth can reuse
the corpus without re-downloading.

  Redis  dd:runs:{run_id}:manifest                    — live snapshot while run is in flight
  MinIO  ingestion/{slug}/manifest.json               — canonical manifest (post-finalize)
  MinIO  ingestion/{slug}/pages/{idx:04d}-{slug}.md   — page bodies

Manifest in Redis is keyed by run_id so the live progress UI can poll one
specific in-flight ingestion. The canonical MinIO manifest is keyed by
framework_slug and is what the library view + cached-check read.
"""
import json
import logging
from dataclasses import asdict, dataclass
from typing import Optional

import redis.asyncio as redis_aio

from .storage_minio import (
    MinIOStorage,
    manifest_key,
    page_key,
    raw_page_key,
    vault_manifest_key,
    vault_sentinelized_key,
)


logger = logging.getLogger(__name__)

_TTL_S = 7200


@dataclass
class ManifestEntry:
    idx: int
    slug: str
    url: str
    tier: str
    bytes: int
    title: str = ""
    key: str = ""        # MinIO key — present once written


def live_manifest_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:manifest"


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
            from services.docs_distiller.synth.corpus_normalize import (
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
        from services.docs_distiller.synth.vault import build_manifest
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
                f"{n} fence(s) → {vk}"
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
