"""Backfills for synth artifacts (vault + normalize) on pages ingested
BEFORE the corresponding `add_page` hooks started persisting them.

Two backfills:

  - `backfill_vaults_for_framework(slug)` / `backfill_all_vaults()`
      build the vault sidecar (.vault.json + .sentinelized.md) for
      every existing page. Idempotent: pages with vault blobs
      already present are skipped.

  - `backfill_normalize_for_framework(slug)` / `backfill_all_normalize()`
      run corpus_normalize on every page, write normalized body back
      over the canonical `ingestion/{slug}/pages/...md` path, and
      preserve the raw at `ingestion-raw/{slug}/pages/...md`.
      After normalize the existing vaults are stale (different bytes
      → different hashes), so the function also force-rebuilds the
      vault on the normalized body.

Per-page failures are logged but never crash the run.

Usage (one-shot via kubectl exec):

    kubectl exec -n coelhonexus-dev <pod> -c coelhonexus-fastapi -- \
      python -c "import asyncio; \
                 from domains.dd.synth.backfill import \
                   backfill_all_normalize; \
                 asyncio.run(backfill_all_normalize())"
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..ingestion.storage import (
    framework_prefix,
    get_storage,
    raw_page_key,
    vault_manifest_key,
    vault_sentinelized_key,
)
from .corpus_normalize import normalize_doc
from .vault import build_manifest


logger = logging.getLogger(__name__)

# Concurrent build/write per framework. ~8 keeps MinIO+local-CPU
# saturated without flooding (build is parse+hash bound, not I/O).
_CONCURRENCY = 8


async def _list_framework_slugs() -> list[str]:
    """Walk MinIO's `ingestion/` prefix and return every framework slug
    that has at least one page."""
    s = get_storage()
    folders = await s.list_subfolders("ingestion/")
    return sorted(f.rstrip("/").rsplit("/", 1)[-1] for f in folders)


async def _list_page_keys(slug: str) -> list[str]:
    s = get_storage()
    return sorted(
        k for k in await s.list(f"{framework_prefix(slug)}pages/")
        if k.endswith(".md")
    )


def _parse_page_key(key: str) -> Optional[tuple[int, str]]:
    """`ingestion/{slug}/pages/{idx:04d}-{page_slug}.md` → (idx, page_slug)."""
    fname = key.rsplit("/", 1)[-1].removesuffix(".md")
    if "-" not in fname:
        return None
    head, _, page_slug = fname.partition("-")
    try:
        idx = int(head)
    except ValueError:
        return None
    return idx, page_slug


async def _vault_exists(slug: str, idx: int, page_slug: str) -> bool:
    """True if BOTH vault blobs are already in MinIO. Treats partial
    state (one but not the other) as missing so we rewrite cleanly."""
    s = get_storage()
    vk = vault_manifest_key(slug, idx, page_slug)
    sk = vault_sentinelized_key(slug, idx, page_slug)
    a, b = await asyncio.gather(s.exists(vk), s.exists(sk))
    return bool(a and b)


async def _backfill_one(
    slug: str, page_key: str, sem: asyncio.Semaphore,
) -> tuple[str, int, str]:
    """Build + write vault for one page. Returns (page_slug, n_fences,
    status) where status ∈ {'built', 'skipped', 'error'}."""
    async with sem:
        parsed = _parse_page_key(page_key)
        if parsed is None:
            return (page_key, 0, "error")
        idx, page_slug = parsed
        try:
            if await _vault_exists(slug, idx, page_slug):
                return (page_slug, 0, "skipped")
            s = get_storage()
            body = await s.read_text(page_key)
            sentinelized, manifest = build_manifest(
                framework=slug, source_key=page_key, md_text=body,
            )
            vk = vault_manifest_key(slug, idx, page_slug)
            sk = vault_sentinelized_key(slug, idx, page_slug)
            await asyncio.gather(
                s.write(vk, manifest.model_dump_json(),
                        content_type="application/json"),
                s.write(sk, sentinelized, content_type="text/markdown"),
            )
            return (page_slug, len(manifest.entries), "built")
        except Exception as e:
            logger.warning(
                f"[backfill] {slug} idx={idx} {page_slug}: "
                f"{type(e).__name__}: {e}"
            )
            return (page_slug, 0, "error")


async def backfill_vaults_for_framework(slug: str) -> dict:
    """Build vaults for every existing page of `slug`. Idempotent.
    Returns counts: pages, built, skipped, errors, total_fences."""
    page_keys = await _list_page_keys(slug)
    if not page_keys:
        return {"slug": slug, "pages": 0, "built": 0,
                "skipped": 0, "errors": 0, "total_fences": 0}
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(*(
        _backfill_one(slug, k, sem) for k in page_keys
    ))
    built = sum(1 for _, _, s in results if s == "built")
    skipped = sum(1 for _, _, s in results if s == "skipped")
    errors = sum(1 for _, _, s in results if s == "error")
    fences = sum(n for _, n, s in results if s == "built")
    return {
        "slug": slug, "pages": len(page_keys),
        "built": built, "skipped": skipped, "errors": errors,
        "total_fences": fences,
    }


async def backfill_all_vaults() -> list[dict]:
    """Backfill vaults for every framework in MinIO."""
    slugs = await _list_framework_slugs()
    if not slugs:
        return []
    print(f"[backfill-vault] discovered {len(slugs)} framework(s): "
          + ", ".join(slugs))
    out = []
    for slug in slugs:
        print(f"[backfill-vault] {slug}: starting…")
        r = await backfill_vaults_for_framework(slug)
        print(
            f"[backfill-vault] {slug}: pages={r['pages']} built={r['built']} "
            f"skipped={r['skipped']} errors={r['errors']} "
            f"fences={r['total_fences']}"
        )
        out.append(r)
    return out


# Backwards-compat alias used by prior one-shot invocations.
backfill_all = backfill_all_vaults


# ── corpus_normalize backfill ────────────────────────────────────────

async def _normalize_one(
    slug: str, page_key_str: str, sem: asyncio.Semaphore,
) -> tuple[str, str]:
    """Normalize one page in place:
       1. read body from canonical ingestion key
       2. write raw to `ingestion-raw/...` (preservation)
       3. normalize the body
       4. write normalized back to canonical key
       5. rebuild vault on normalized body (overwrite existing)
    Returns (page_slug, status) where status ∈ {'normalized', 'unchanged', 'error'}.
    Idempotent because normalize_doc is idempotent — same input → same
    output → same hashes; on a 2nd run nothing functionally changes."""
    async with sem:
        parsed = _parse_page_key(page_key_str)
        if parsed is None:
            return (page_key_str, "error")
        idx, page_slug = parsed
        try:
            s = get_storage()
            body = await s.read_text(page_key_str)
            normalized = normalize_doc(body).body
            changed = normalized != body
            # Always preserve raw (cheap; existing key just overwrites
            # with the same bytes on subsequent runs).
            raw_k = raw_page_key(slug, idx, page_slug)
            await s.write(raw_k, body, content_type="text/markdown")
            if changed:
                # Replace canonical with normalized.
                await s.write(
                    page_key_str, normalized, content_type="text/markdown",
                )
            # Rebuild vault on the normalized body — the existing vault
            # was hashed against the raw body, so post-normalize it's
            # stale. Always write so the vault keys match the bytes
            # downstream consumers will read.
            sentinelized, manifest = build_manifest(
                framework=slug, source_key=page_key_str, md_text=normalized,
            )
            vk = vault_manifest_key(slug, idx, page_slug)
            sk = vault_sentinelized_key(slug, idx, page_slug)
            await asyncio.gather(
                s.write(
                    vk, manifest.model_dump_json(),
                    content_type="application/json",
                ),
                s.write(sk, sentinelized, content_type="text/markdown"),
            )
            return (page_slug, "normalized" if changed else "unchanged")
        except Exception as e:
            logger.warning(
                f"[backfill-normalize] {slug} idx={idx} {page_slug}: "
                f"{type(e).__name__}: {e}"
            )
            return (page_slug, "error")


async def backfill_normalize_for_framework(slug: str) -> dict:
    """Normalize every existing page of `slug` + rebuild vaults on the
    normalized bytes. Idempotent. Returns counts."""
    page_keys = await _list_page_keys(slug)
    if not page_keys:
        return {"slug": slug, "pages": 0, "normalized": 0,
                "unchanged": 0, "errors": 0}
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(*(
        _normalize_one(slug, k, sem) for k in page_keys
    ))
    normalized = sum(1 for _, s in results if s == "normalized")
    unchanged = sum(1 for _, s in results if s == "unchanged")
    errors = sum(1 for _, s in results if s == "error")
    return {
        "slug": slug, "pages": len(page_keys),
        "normalized": normalized, "unchanged": unchanged,
        "errors": errors,
    }


async def backfill_all_normalize() -> list[dict]:
    """Normalize every page of every framework, rebuild vaults."""
    slugs = await _list_framework_slugs()
    if not slugs:
        return []
    print(f"[backfill-normalize] discovered {len(slugs)} framework(s): "
          + ", ".join(slugs))
    out = []
    for slug in slugs:
        print(f"[backfill-normalize] {slug}: starting…")
        r = await backfill_normalize_for_framework(slug)
        print(
            f"[backfill-normalize] {slug}: pages={r['pages']} "
            f"normalized={r['normalized']} unchanged={r['unchanged']} "
            f"errors={r['errors']}"
        )
        out.append(r)
    return out
