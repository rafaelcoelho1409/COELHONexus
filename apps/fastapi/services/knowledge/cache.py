"""
Knowledge Distiller — Cache Layer

Two-tier cache keyed by content identity so repeated runs reuse expensive work:

  Layer 1 — Ingestion cache:     `_cache/ingestion/{framework}/{version}/`
     SHARED across all users and levels. Coverage (raw docs) is
     framework+version-dependent, independent of the learner's profile.

  Layer 2 — Planning cache:      `_cache/planning/{framework}/{version}/`
     SHARED with ingestion. The planner's chapter decomposition depends
     only on the corpus, not on learner profile.

  Layer 3 — Synthesis cache:     `_cache/synthesis/{framework}/{version}/{profile_hash}/`
     PER-PROFILE. Synthesis tone (code density, skipped intros, portfolio
     refs) differs with the profile, so caches cannot be shared across
     different UserProfiles. profile_hash is a SHA256 of canonical JSON.

FRESHNESS:
  - `version="latest"` entries get a TTL (default 14 days). After that,
    they're considered stale and re-fetched on the next request.
  - Pinned versions (e.g. "2.11.1") are treated as IMMUTABLE — cache
    never expires. You can only purge them manually or via `force_refresh`.

STORAGE:
  All cache data lives in the SAME MinIO bucket as study artifacts,
  under the `_cache/` prefix. The bucket's lifecycle policies (if any)
  should preserve `_cache/**` across study-folder cleanups.

FAILURE HANDLING:
  Cache writes are best-effort — if a write fails, log + continue.
  The node's primary job (produce the artifact in study_root) must not
  fail because the cache couldn't be updated.
"""
import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# =============================================================================
# Fresh-state metadata written alongside every cached artifact group
# =============================================================================
class CacheState(BaseModel):
    """Bookkeeping file written into each cache directory."""
    framework: str
    version: str                       # "latest" or pinned version string
    profile_hash: Optional[str] = None  # set only on synthesis caches
    cached_at: str                     # ISO timestamp
    source_study_root: str             # which study_root populated this cache
    manifest_hash: Optional[str] = None  # for ingest+plan caches — ties them together


class IngestCacheEntry(BaseModel):
    """What the ingest cache returns on hit."""
    raw_keys: list[str]                # MinIO keys under _cache/ingestion/.../raw/
    manifest: list[dict]
    manifest_hash: str                 # SHA256 of sorted slug list
    tier_used: str
    cached_at: str


class PlanCacheEntry(BaseModel):
    plan_key: str                      # MinIO key of plan.json
    manifest_hash: str                 # must match the ingest cache's hash
    cached_at: str


class ChapterCacheEntry(BaseModel):
    readme_key: str
    challenges_key: str
    flashcards_key: str
    score: float
    iterations: int
    cached_at: str


# =============================================================================
# Helpers
# =============================================================================
def _slug(s: str) -> str:
    """Filesystem-safe lowercase version of a string."""
    out = s.lower().strip().replace(" ", "-")
    out = re.sub(r"[^a-z0-9._\-]+", "-", out)
    return out or "unknown"


def _version_slug(version: str | None) -> str:
    """`None` → 'latest'; else the version string itself (lowercased)."""
    if not version:
        return "latest"
    return _slug(version)


def canonical_profile_hash(user_profile_dict: dict) -> str:
    """
    Deterministic hash of a UserProfile's full contents. Any difference in
    level / target_markets / mastered_technologies / portfolio_refs /
    acceptance_threshold produces a different hash → a separate cache bucket.
    """
    canonical = json.dumps(user_profile_dict, sort_keys = True, separators = (",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_manifest_hash(slugs: list[str]) -> str:
    """Hash the sorted slug list — identity of the raw corpus."""
    canonical = "\n".join(sorted(slugs))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# The cache
# =============================================================================
class StudyCache:
    """
    Read/write cache across the three pipeline stages. All operations take
    explicit framework + version so callers never have to construct keys.
    """

    def __init__(
        self,
        storage: MinIOStudyStorage,
        latest_ttl_days: int = 14):
        self.storage = storage
        self._ttl_seconds = latest_ttl_days * 86400

    # -------------------------------------------------------------------------
    # Key builders
    # -------------------------------------------------------------------------
    def _ingest_prefix(self, framework: str, version: str | None) -> str:
        return f"_cache/ingestion/{_slug(framework)}/{_version_slug(version)}"

    def _plan_prefix(self, framework: str, version: str | None) -> str:
        return f"_cache/planning/{_slug(framework)}/{_version_slug(version)}"

    def _synth_prefix(
        self,
        framework: str,
        version: str | None,
        profile_hash: str,
        chapter_num: int) -> str:
        return (
            f"_cache/synthesis/{_slug(framework)}/{_version_slug(version)}/"
            f"{profile_hash}/chapter{chapter_num:02d}"
        )

    # -------------------------------------------------------------------------
    # Freshness check
    # -------------------------------------------------------------------------
    def _is_fresh(self, state: CacheState) -> bool:
        """True if cache entry should be considered valid right now."""
        if state.version != "latest":
            return True  # pinned versions are immutable
        try:
            cached_at = datetime.fromisoformat(state.cached_at)
        except Exception:
            return False
        now = datetime.now(timezone.utc)
        # Handle naive datetime from older entries
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo = timezone.utc)
        age = (now - cached_at).total_seconds()
        return age < self._ttl_seconds

    # -------------------------------------------------------------------------
    # Generic state I/O
    # -------------------------------------------------------------------------
    async def _read_state(self, prefix: str) -> Optional[CacheState]:
        key = f"{prefix}/_state.json"
        if not await self.storage.exists(key):
            return None
        try:
            raw = await self.storage.read_text(key)
            return CacheState.model_validate_json(raw)
        except Exception as e:
            logger.warning(f"[cache] failed to read {key}: {e}")
            return None

    async def _write_state(self, prefix: str, state: CacheState) -> None:
        key = f"{prefix}/_state.json"
        try:
            await self.storage.write(
                key, state.model_dump_json(indent = 2),
                content_type = "application/json",
            )
        except Exception as e:
            logger.warning(f"[cache] failed to write {key}: {e}")

    # =========================================================================
    # Ingestion cache
    # =========================================================================
    async def get_ingestion(
        self,
        framework: str,
        version: str | None,
        force_refresh: bool = False) -> Optional[IngestCacheEntry]:
        """
        Return a cache hit or None. On hit, raw files live at
        `_cache/ingestion/.../raw/*.md` — caller must copy them into
        its own study_root/research/raw/.
        """
        if force_refresh:
            return None
        prefix = self._ingest_prefix(framework, version)
        state = await self._read_state(prefix)
        if state is None or not self._is_fresh(state):
            return None
        raw_keys = await self.storage.list(f"{prefix}/raw/")
        md_keys = [k for k in raw_keys if k.endswith(".md")]
        if not md_keys:
            return None
        # Read manifest
        manifest_key = f"{prefix}/manifest.json"
        if not await self.storage.exists(manifest_key):
            return None
        try:
            manifest = json.loads(await self.storage.read_text(manifest_key))
        except Exception:
            return None
        slugs = [e.get("slug", "") for e in manifest]
        return IngestCacheEntry(
            raw_keys = md_keys,
            manifest = manifest,
            manifest_hash = compute_manifest_hash(slugs),
            tier_used = (manifest[0].get("tier") if manifest else "unknown"),
            cached_at = state.cached_at,
        )

    async def copy_ingestion_to_study(
        self,
        framework: str,
        version: str | None,
        study_root: str) -> int:
        """
        Copy every `_cache/.../raw/*.md` + manifest.json into `study_root`.
        Returns byte count copied. Uses parallel reads with a small batch.
        """
        prefix = self._ingest_prefix(framework, version)
        raw_keys = await self.storage.list(f"{prefix}/raw/")
        md_keys = sorted(k for k in raw_keys if k.endswith(".md"))
        total_bytes = 0

        # Server-side CopyObject (2026-04-21): ~1 RTT per file, no bytes
        # through the client. 996-file restore went from ~3 min (read+write)
        # to <10s with this path.
        async def _copy_one(src: str) -> int:
            slug = src.rsplit("/", 1)[-1]
            dest = f"{study_root}/research/raw/{slug}"
            try:
                return await self.storage.copy(src, dest)
            except Exception:
                # Fallback to read+write if server-side copy isn't supported
                data = await self.storage.read(src)
                return await self.storage.write(
                    dest, data, content_type = "text/markdown",
                )

        # Parallel in batches of 20
        for i in range(0, len(md_keys), 20):
            batch = md_keys[i : i + 20]
            sizes = await asyncio.gather(*(_copy_one(k) for k in batch))
            total_bytes += sum(sizes)

        # Manifest — server-side copy too
        manifest_src = f"{prefix}/manifest.json"
        if await self.storage.exists(manifest_src):
            try:
                total_bytes += await self.storage.copy(
                    manifest_src,
                    f"{study_root}/research/manifest.json",
                )
            except Exception:
                data = await self.storage.read(manifest_src)
                await self.storage.write(
                    f"{study_root}/research/manifest.json",
                    data, content_type = "application/json",
                )
                total_bytes += len(data)
        logger.info(
            f"[cache] copied {len(md_keys)} ingest files + manifest "
            f"({total_bytes}B) from cache → {study_root}"
        )
        return total_bytes

    # ------ Streaming-ingest support (per-page, resume-capable) ------
    async def get_cached_slugs(
        self,
        framework: str,
        version: str | None) -> set[str]:
        """
        List slug filenames present in the ingestion cache for
        (framework, version). Returned slugs omit the `.md` suffix.

        Used by the streaming ingest path to skip URLs whose content was
        already fetched by a previous (maybe crashed) run. Provides
        resume-from-partial semantics: each crawl attempt continues where
        the previous one stopped, no wasted HTTP/Playwright work.

        Does NOT check whether the cache is COMPLETE (for that, see
        `get_ingestion`). Returns slugs even during an in-flight cache
        population, which is precisely the resume case we want.
        """
        prefix = f"{self._ingest_prefix(framework, version)}/raw/"
        keys = await self.storage.list(prefix)
        out: set[str] = set()
        for k in keys:
            if not k.endswith(".md"):
                continue
            fname = k.rsplit("/", 1)[-1]
            out.add(fname[:-3])  # strip ".md"
        return out

    async def save_ingested_page(
        self,
        framework: str,
        version: str | None,
        study_root: str,
        slug: str,
        content: str,
        url: str,
        tier: str) -> int:
        """
        Write one page's markdown to BOTH the ingest cache and the study's
        research/raw/ prefix. Returns bytes written. Mirrors the shape of
        `services/knowledge/ingestion._write_raw` but tees the result to
        the cache so partial progress survives a task restart.

        Additionally writes a small JSON sidecar (`{slug}.meta.json`) next
        to the cached markdown so future resume runs can recover the
        original URL + tier without scraping the page again. This is what
        allows BFS-mode resume: we rebuild a URL → slug map even when the
        only state left from a crashed run is the cache itself.
        """
        cache_prefix = self._ingest_prefix(framework, version)
        cache_key = f"{cache_prefix}/raw/{slug}.md"
        meta_key = f"{cache_prefix}/raw/{slug}.meta.json"
        study_key = f"{study_root}/research/raw/{slug}.md"
        body = content.encode("utf-8") if isinstance(content, str) else content
        meta_body = json.dumps(
            {"url": url, "tier": tier, "slug": slug},
            separators = (",", ":"),
        )
        # Write all three in parallel; cache is best-effort but study_root is required
        results = await asyncio.gather(
            self.storage.write(study_key, body, content_type = "text/markdown"),
            self.storage.write(cache_key, body, content_type = "text/markdown"),
            self.storage.write(meta_key, meta_body, content_type = "application/json"),
            return_exceptions = True,
        )
        study_bytes = results[0]
        if isinstance(study_bytes, Exception):
            raise study_bytes
        if isinstance(results[1], Exception):
            logger.warning(
                f"[cache] save_ingested_page: study write OK but cache write "
                f"failed for {slug}: {results[1]}"
            )
        if isinstance(results[2], Exception):
            logger.warning(
                f"[cache] save_ingested_page: meta sidecar write failed "
                f"for {slug}: {results[2]}"
            )
        return study_bytes

    async def get_cached_manifest(
        self,
        framework: str,
        version: str | None) -> list[dict]:
        """
        Return a list of `{slug, url, tier}` dicts for every page in the
        ingestion cache. Reads the per-slug `.meta.json` sidecars that
        `save_ingested_page` writes. Used by the streaming ingest path to
        reconstruct URL → slug mapping for resume — even in BFS mode,
        where the initial URL list is empty.

        Slugs with a `.md` but no sidecar are returned with `url=None`
        (older cache entries predating the sidecar). Callers should
        treat those as slug-only resume entries.
        """
        prefix = f"{self._ingest_prefix(framework, version)}/raw/"
        keys = await self.storage.list(prefix)
        # Collect slug → has_md, meta_key
        by_slug: dict[str, dict] = {}
        for k in keys:
            fname = k.rsplit("/", 1)[-1]
            if fname.endswith(".meta.json"):
                slug = fname[: -len(".meta.json")]
                by_slug.setdefault(slug, {})["meta_key"] = k
            elif fname.endswith(".md"):
                slug = fname[: -len(".md")]
                by_slug.setdefault(slug, {})["has_md"] = True
        # Read meta sidecars in parallel, drop entries without a body file
        slugs_with_md = [s for s, v in by_slug.items() if v.get("has_md")]

        async def _load_meta(slug: str) -> dict:
            meta_key = by_slug[slug].get("meta_key")
            out: dict = {"slug": slug, "url": None, "tier": "crawl4ai"}
            if not meta_key:
                return out
            try:
                raw = await self.storage.read_text(meta_key)
                parsed = json.loads(raw)
                out["url"] = parsed.get("url")
                if parsed.get("tier"):
                    out["tier"] = parsed["tier"]
            except Exception as e:
                logger.warning(f"[cache] failed to read meta for {slug}: {e}")
            return out
        # Batched reads — an unbatched gather of 884 concurrent aioboto3
        # client contexts saturates MinIO's HTTP pool (same class of
        # issue as restore: observed to take ~100s when launched all at
        # once). Chunks of 20 finish in seconds.
        _META_BATCH = 20
        entries: list[dict] = []
        for i in range(0, len(slugs_with_md), _META_BATCH):
            chunk = slugs_with_md[i : i + _META_BATCH]
            results = await asyncio.gather(
                *(_load_meta(s) for s in chunk),
                return_exceptions = True,
            )
            for r in results:
                if isinstance(r, dict):
                    entries.append(r)
                elif isinstance(r, Exception):
                    logger.warning(f"[cache] get_cached_manifest: {r}")
        return entries

    async def save_sidecar_only(
        self,
        framework: str,
        version: str | None,
        slug: str,
        url: str,
        tier: str = "crawl4ai") -> None:
        """
        Backfill a `.meta.json` sidecar for a slug that exists in the
        cache but lacks URL metadata (entries saved before the sidecar
        mechanism existed).

        Called from the stream consumer when BFS re-fetches a
        previously-cached slug-only URL: we capture the live URL and
        persist it so future BFS runs can URL-filter this page before
        fetching. One-time tax — after the backfill, subsequent runs
        skip the URL entirely.
        """
        meta_key = f"{self._ingest_prefix(framework, version)}/raw/{slug}.meta.json"
        meta_body = json.dumps(
            {"url": url, "tier": tier, "slug": slug},
            separators = (",", ":"),
        )
        await self.storage.write(
            meta_key, meta_body, content_type = "application/json",
        )

    async def copy_ingested_page_to_study(
        self,
        framework: str,
        version: str | None,
        slug: str,
        study_root: str) -> int:
        """
        Copy a single already-cached page from `_cache/ingestion/.../raw/`
        into `<study_root>/research/raw/`. Returns bytes.

        Fast path: if the destination already has the object (common after
        folder unification dropped the timestamp from study_root — the
        study folder IS now the per-identity location), skip the copy
        entirely. Returns the existing object's byte count.

        Otherwise: server-side CopyObject (one RTT, no body transfer) —
        orders of magnitude faster than the old read→write path, which
        moved the bytes through the client process and saturated MinIO's
        HTTP pool on bulk restores.
        """
        cache_key = f"{self._ingest_prefix(framework, version)}/raw/{slug}.md"
        study_key = f"{study_root}/research/raw/{slug}.md"
        # Fast path — slug already materialized at destination.
        if await self.storage.exists(study_key):
            head_bytes = await self._size_of(study_key)
            return head_bytes
        # Server-side copy.
        return await self.storage.copy(cache_key, study_key)

    async def _size_of(self, key: str) -> int:
        """Return object size in bytes; 0 if the key vanished between check+read."""
        async with self.storage._client() as s3:
            try:
                head = await s3.head_object(Bucket = self.storage.bucket, Key = key)
                return int(head.get("ContentLength") or 0)
            except Exception:
                return 0

    async def finalize_ingestion(
        self,
        framework: str,
        version: str | None,
        study_root: str,
        manifest: list[dict],
        slugs: list[str]) -> None:
        """
        Finalize an ingest cache entry after streaming completion. Writes
        `manifest.json` + the `_state.json` completeness marker. The raw
        files themselves were already written per-page via
        `save_ingested_page` during streaming, so this is just metadata.

        Call once at the END of a successful `ingest_framework_docs` run.
        Before this call, `get_ingestion(..)` returns None (incomplete) and
        `get_cached_slugs(..)` returns the partial set (supports resume).
        After, `get_ingestion(..)` succeeds and serves future hits.
        """
        prefix = self._ingest_prefix(framework, version)
        await self.storage.write(
            f"{prefix}/manifest.json",
            json.dumps(manifest, indent = 2),
            content_type = "application/json",
        )
        state = CacheState(
            framework = framework,
            version = _version_slug(version),
            cached_at = datetime.now(timezone.utc).isoformat(),
            source_study_root = study_root,
            manifest_hash = compute_manifest_hash(slugs),
        )
        await self._write_state(prefix, state)
        logger.info(
            f"[cache] finalized ingest cache {prefix} "
            f"({len(slugs)} slugs, manifest_hash={state.manifest_hash})"
        )

    async def set_ingestion(
        self,
        framework: str,
        version: str | None,
        study_root: str,
        manifest: list[dict],
        slugs: list[str]) -> None:
        """
        Copy freshly-crawled raw files from `study_root/research/raw/` into
        the cache. Called after a successful ingest so the next run with the
        same (framework, version) gets a cache hit.
        """
        prefix = self._ingest_prefix(framework, version)
        # Copy raw files
        raw_src_prefix = f"{study_root}/research/raw/"
        src_keys = await self.storage.list(raw_src_prefix)
        md_keys = sorted(k for k in src_keys if k.endswith(".md"))

        async def _copy(src: str) -> None:
            slug = src.rsplit("/", 1)[-1]
            dest = f"{prefix}/raw/{slug}"
            if await self.storage.exists(dest):
                return  # don't re-write identical content
            data = await self.storage.read(src)
            await self.storage.write(dest, data, content_type = "text/markdown")

        for i in range(0, len(md_keys), 20):
            await asyncio.gather(*(_copy(k) for k in md_keys[i : i + 20]))

        # Manifest
        await self.storage.write(
            f"{prefix}/manifest.json",
            json.dumps(manifest, indent = 2),
            content_type = "application/json",
        )

        # State
        state = CacheState(
            framework = framework,
            version = _version_slug(version),
            cached_at = datetime.now(timezone.utc).isoformat(),
            source_study_root = study_root,
            manifest_hash = compute_manifest_hash(slugs),
        )
        await self._write_state(prefix, state)
        logger.info(
            f"[cache] wrote ingest cache {prefix} ({len(md_keys)} files)"
        )

    # =========================================================================
    # Planning cache
    # =========================================================================
    async def get_plan(
        self,
        framework: str,
        version: str | None,
        current_manifest_hash: str,
        force_refresh: bool = False) -> Optional[PlanCacheEntry]:
        """
        Return a plan cache hit only when the manifest_hash MATCHES — the
        plan references file slugs, so it's only valid for the same corpus
        identity. If the raw files changed (even same framework+version),
        cache miss.
        """
        if force_refresh:
            return None
        prefix = self._plan_prefix(framework, version)
        state = await self._read_state(prefix)
        if state is None or not self._is_fresh(state):
            return None
        if state.manifest_hash != current_manifest_hash:
            logger.info(
                f"[cache] plan cache stale: manifest_hash mismatch "
                f"(cached={state.manifest_hash}, current={current_manifest_hash})"
            )
            return None
        plan_key = f"{prefix}/plan.json"
        if not await self.storage.exists(plan_key):
            return None
        return PlanCacheEntry(
            plan_key = plan_key,
            manifest_hash = state.manifest_hash,
            cached_at = state.cached_at,
        )

    async def copy_plan_to_study(
        self,
        framework: str,
        version: str | None,
        study_root: str) -> None:
        src = f"{self._plan_prefix(framework, version)}/plan.json"
        if not await self.storage.exists(src):
            raise FileNotFoundError(f"plan cache missing at {src}")
        data = await self.storage.read(src)
        await self.storage.write(
            f"{study_root}/research/plan.json", data,
            content_type = "application/json",
        )
        logger.info(f"[cache] copied plan from cache → {study_root}/research/plan.json")

    async def set_plan(
        self,
        framework: str,
        version: str | None,
        study_root: str,
        manifest_hash: str) -> None:
        """Copy the freshly-written plan.json into the cache."""
        prefix = self._plan_prefix(framework, version)
        src = f"{study_root}/research/plan.json"
        if not await self.storage.exists(src):
            logger.warning(f"[cache] set_plan: source missing at {src}")
            return
        data = await self.storage.read(src)
        await self.storage.write(
            f"{prefix}/plan.json", data,
            content_type = "application/json",
        )
        state = CacheState(
            framework = framework,
            version = _version_slug(version),
            cached_at = datetime.now(timezone.utc).isoformat(),
            source_study_root = study_root,
            manifest_hash = manifest_hash,
        )
        await self._write_state(prefix, state)
        logger.info(f"[cache] wrote plan cache {prefix}")

    # =========================================================================
    # Synthesis cache — per chapter, per profile
    # =========================================================================
    async def get_chapter(
        self,
        framework: str,
        version: str | None,
        profile_hash: str,
        chapter_num: int,
        chapter_title: str,
        assigned_files: list[str],
        force_refresh: bool = False) -> Optional[ChapterCacheEntry]:
        """
        Chapter cache is keyed by (framework, version, profile_hash, chapter_num).
        Additionally checks that the chapter's title + assigned_files match —
        if the planner decomposed the corpus differently this run, the cached
        chapter is for a different problem and must be regenerated.
        """
        if force_refresh:
            return None
        prefix = self._synth_prefix(framework, version, profile_hash, chapter_num)
        state = await self._read_state(prefix)
        if state is None or not self._is_fresh(state):
            return None
        # Check chapter identity
        identity_key = f"{prefix}/_identity.json"
        if not await self.storage.exists(identity_key):
            return None
        try:
            identity = json.loads(await self.storage.read_text(identity_key))
        except Exception:
            return None
        if identity.get("title") != chapter_title:
            return None
        if sorted(identity.get("assigned_files") or []) != sorted(assigned_files):
            return None
        # Read grader state
        grader_key = f"{prefix}/_grader.json"
        if not await self.storage.exists(grader_key):
            return None
        try:
            grader = json.loads(await self.storage.read_text(grader_key))
        except Exception:
            return None
        # All three artifacts must be present
        readme = f"{prefix}/README.md"
        challenges = f"{prefix}/challenges.md"
        flashcards = f"{prefix}/flashcards.json"
        for k in (readme, challenges, flashcards):
            if not await self.storage.exists(k):
                return None
        return ChapterCacheEntry(
            readme_key = readme,
            challenges_key = challenges,
            flashcards_key = flashcards,
            score = float(grader.get("score") or 0.0),
            iterations = int(grader.get("iterations") or 0),
            cached_at = state.cached_at,
        )

    async def copy_chapter_to_study(
        self,
        framework: str,
        version: str | None,
        profile_hash: str,
        chapter_num: int,
        study_root: str) -> dict:
        """
        Copy cached chapter artifacts into the study_root. Returns the paths
        dict in the shape the synthesize_chapter node returns.
        """
        prefix = self._synth_prefix(framework, version, profile_hash, chapter_num)
        dest_prefix = f"{study_root}/chapter{chapter_num:02d}"
        src_readme = f"{prefix}/README.md"
        src_challenges = f"{prefix}/challenges.md"
        src_flashcards = f"{prefix}/flashcards.json"
        dest_readme = f"{dest_prefix}/README.md"
        dest_challenges = f"{dest_prefix}/challenges.md"
        dest_flashcards = f"{dest_prefix}/flashcards.json"

        async def _copy(src: str, dst: str, ct: str) -> None:
            data = await self.storage.read(src)
            await self.storage.write(dst, data, content_type = ct)

        await asyncio.gather(
            _copy(src_readme, dest_readme, "text/markdown"),
            _copy(src_challenges, dest_challenges, "text/markdown"),
            _copy(src_flashcards, dest_flashcards, "application/json"),
        )
        logger.info(
            f"[cache] copied chapter {chapter_num} from cache → {dest_prefix}"
        )
        return {
            "number": chapter_num,
            "content_path": dest_readme,
            "challenges_path": dest_challenges,
            "flashcards_path": dest_flashcards,
        }

    async def set_chapter(
        self,
        framework: str,
        version: str | None,
        profile_hash: str,
        chapter_num: int,
        chapter_title: str,
        assigned_files: list[str],
        study_root: str,
        score: float,
        iterations: int) -> None:
        """Copy accepted-chapter artifacts from study_root into cache."""
        prefix = self._synth_prefix(framework, version, profile_hash, chapter_num)
        src_prefix = f"{study_root}/chapter{chapter_num:02d}"

        async def _copy(rel: str, content_type: str) -> None:
            src = f"{src_prefix}/{rel}"
            dst = f"{prefix}/{rel}"
            if not await self.storage.exists(src):
                logger.warning(f"[cache] set_chapter: source missing at {src}")
                return
            data = await self.storage.read(src)
            await self.storage.write(dst, data, content_type = content_type)

        await asyncio.gather(
            _copy("README.md", "text/markdown"),
            _copy("challenges.md", "text/markdown"),
            _copy("flashcards.json", "application/json"),
        )
        # Identity + grader metadata
        await self.storage.write(
            f"{prefix}/_identity.json",
            json.dumps({
                "title": chapter_title,
                "assigned_files": sorted(assigned_files),
            }, indent = 2),
            content_type = "application/json",
        )
        await self.storage.write(
            f"{prefix}/_grader.json",
            json.dumps({
                "score": score,
                "iterations": iterations,
            }, indent = 2),
            content_type = "application/json",
        )
        state = CacheState(
            framework = framework,
            version = _version_slug(version),
            profile_hash = profile_hash,
            cached_at = datetime.now(timezone.utc).isoformat(),
            source_study_root = study_root,
        )
        await self._write_state(prefix, state)
        logger.info(
            f"[cache] wrote chapter cache {prefix} (score={score:.2f})"
        )
