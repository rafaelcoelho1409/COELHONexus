"""Vault + corpus-normalize backfills.

Run on pages that were ingested BEFORE the corresponding `add_page` hooks
started persisting these artifacts. Idempotent + per-page best-effort.

Vault backfill:
  build .vault.json + .sentinelized.md for every existing page; pages with
  both blobs already present are skipped.

Normalize backfill:
  run corpus_normalize on every page, write the normalized body back over
  the canonical `ingestion/{slug}/pages/...md`, preserve the raw at
  `ingestion-raw/`, and force-rebuild the vault on the normalized body
  (existing vaults are stale post-normalize — different bytes, different hashes).

Usage (one-shot via kubectl exec):

    kubectl exec -n coelhonexus-dev <pod> -c coelhonexus-fastapi -- \\
      python -c "import asyncio; \\
                 from domains.dd.synth.backfill import \\
                   backfill_all_normalize; \\
                 asyncio.run(backfill_all_normalize())"
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ...ingestion.storage import (
    framework_prefix,
    get_storage,
    raw_page_key,
    vault_manifest_key,
    vault_sentinelized_key,
)
from ..corpus_normalize import normalize_doc
from ..params import BACKFILL_CONCURRENCY
from ..vault import build_manifest


logger = logging.getLogger(__name__)


async def _list_framework_slugs() -> list[str]:
    """Walk MinIO `ingestion/` and return every framework slug with ≥1 page."""
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
    """True iff BOTH vault blobs are present. Partial state → missing."""
    s = get_storage()
    vk = vault_manifest_key(slug, idx, page_slug)
    sk = vault_sentinelized_key(slug, idx, page_slug)
    a, b = await asyncio.gather(s.exists(vk), s.exists(sk))
    return bool(a and b)


async def _backfill_one(
    slug: str, page_key: str, sem: asyncio.Semaphore,
) -> tuple[str, int, str]:
    """Build + write vault for one page. Returns (page_slug, n_fences, status)
    where status ∈ {'built', 'skipped', 'error'}."""
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
                framework = slug, source_key = page_key, md_text = body,
            )
            vk = vault_manifest_key(slug, idx, page_slug)
            sk = vault_sentinelized_key(slug, idx, page_slug)
            await asyncio.gather(
                s.write(vk, manifest.model_dump_json(),
                        content_type = "application/json"),
                s.write(sk, sentinelized, content_type = "text/markdown"),
            )
            return (page_slug, len(manifest.entries), "built")
        except Exception as e:
            logger.warning(
                f"[backfill] {slug} idx = {idx} {page_slug}: "
                f"{type(e).__name__}: {e}"
            )
            return (page_slug, 0, "error")


async def backfill_vaults_for_framework(slug: str) -> dict:
    """Build vaults for every existing page of `slug`. Idempotent."""
    page_keys = await _list_page_keys(slug)
    if not page_keys:
        return {"slug": slug, "pages": 0, "built": 0,
                "skipped": 0, "errors": 0, "total_fences": 0}
    sem = asyncio.Semaphore(BACKFILL_CONCURRENCY)
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
            f"[backfill-vault] {slug}: pages = {r['pages']} built = {r['built']} "
            f"skipped = {r['skipped']} errors = {r['errors']} "
            f"fences = {r['total_fences']}"
        )
        out.append(r)
    return out


# Backwards-compat alias used by prior one-shot invocations.
backfill_all = backfill_all_vaults


async def _normalize_one(
    slug: str, page_key_str: str, sem: asyncio.Semaphore,
) -> tuple[str, str]:
    """Normalize one page in place: read body → write raw to ingestion-raw/
    → normalize → write back to canonical → rebuild vault on the normalized
    body. Idempotent because normalize_doc is idempotent.
    Returns (page_slug, status) ∈ {'normalized', 'unchanged', 'error'}."""
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
            # Raw always preserved; cheap idempotent overwrite on subsequent runs.
            raw_k = raw_page_key(slug, idx, page_slug)
            await s.write(raw_k, body, content_type = "text/markdown")
            if changed:
                await s.write(
                    page_key_str, normalized, content_type = "text/markdown",
                )
            # Vault rebuild on normalized body — existing vault was hashed
            # against raw, so post-normalize it's stale.
            sentinelized, manifest = build_manifest(
                framework = slug, source_key = page_key_str, md_text = normalized,
            )
            vk = vault_manifest_key(slug, idx, page_slug)
            sk = vault_sentinelized_key(slug, idx, page_slug)
            await asyncio.gather(
                s.write(
                    vk, manifest.model_dump_json(),
                    content_type = "application/json",
                ),
                s.write(sk, sentinelized, content_type = "text/markdown"),
            )
            return (page_slug, "normalized" if changed else "unchanged")
        except Exception as e:
            logger.warning(
                f"[backfill-normalize] {slug} idx = {idx} {page_slug}: "
                f"{type(e).__name__}: {e}"
            )
            return (page_slug, "error")


async def backfill_normalize_for_framework(slug: str) -> dict:
    """Normalize every existing page of `slug` + rebuild vaults. Idempotent."""
    page_keys = await _list_page_keys(slug)
    if not page_keys:
        return {"slug": slug, "pages": 0, "normalized": 0,
                "unchanged": 0, "errors": 0}
    sem = asyncio.Semaphore(BACKFILL_CONCURRENCY)
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
    """Normalize every page of every framework + rebuild vaults."""
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
            f"[backfill-normalize] {slug}: pages = {r['pages']} "
            f"normalized = {r['normalized']} unchanged = {r['unchanged']} "
            f"errors = {r['errors']}"
        )
        out.append(r)
    return out
