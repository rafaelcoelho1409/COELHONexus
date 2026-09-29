"""Vault + corpus-normalize backfills for pages ingested before the add_page hooks ran."""
from __future__ import annotations
import domains
from . import domain

import asyncio
import logging
from typing import Awaitable, Callable, Optional



logger = logging.getLogger(__name__)


async def _list_framework_slugs() -> list[str]:
    """Walk MinIO `ingestion/` and return every framework slug with ≥1 page."""
    s = domains.dd.ingestion.storage.service.get_storage()
    folders = await s.list_subfolders("ingestion/")
    return sorted(f.rstrip("/").rsplit("/", 1)[-1] for f in folders)


async def _list_page_keys(slug: str) -> list[str]:
    s = domains.dd.ingestion.storage.service.get_storage()
    return sorted(
        k for k in await s.list(f"{domains.dd.ingestion.storage.keys.framework_prefix(slug)}pages/")
        if k.endswith(".md")
    )


async def _vault_exists(slug: str, idx: int, page_slug: str) -> bool:
    """True iff BOTH vault blobs are present. Partial state → missing."""
    s = domains.dd.ingestion.storage.service.get_storage()
    vk = domains.dd.ingestion.storage.keys.vault_manifest_key(slug, idx, page_slug)
    sk = domains.dd.ingestion.storage.keys.vault_sentinelized_key(slug, idx, page_slug)
    a, b = await asyncio.gather(s.exists(vk), s.exists(sk))
    return bool(a and b)


async def _backfill_one(
    slug: str, page_key: str, sem: asyncio.Semaphore,
) -> tuple[str, int, str]:
    """Build + write vault for one page. Returns (page_slug, n_fences, status)
    where status ∈ {'built', 'skipped', 'error'}."""
    async with sem:
        parsed = domain.parse_page_key(page_key)
        if parsed is None:
            return (page_key, 0, "error")
        idx, page_slug = parsed
        try:
            if await _vault_exists(slug, idx, page_slug):
                return (page_slug, 0, "skipped")
            s = domains.dd.ingestion.storage.service.get_storage()
            body = await s.read_text(page_key)
            sentinelized, manifest = domains.dd.synth.nodes.vault.domain.build_manifest(
                framework = slug, source_key = page_key, md_text = body,
            )
            vk = domains.dd.ingestion.storage.keys.vault_manifest_key(slug, idx, page_slug)
            sk = domains.dd.ingestion.storage.keys.vault_sentinelized_key(slug, idx, page_slug)
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


async def backfill_vaults_for_framework(
    slug: str,
    on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> dict:
    """Build vaults for every existing page of `slug`. Idempotent.
    `on_progress(done, total)` is awaited after each page finishes (built,
    skipped, or errored alike) — lets a caller (ingestion's dispatch) surface
    live progress instead of this whole call being one silent multi-second
    block on a large framework."""
    page_keys = await _list_page_keys(slug)
    if not page_keys:
        if on_progress:
            await on_progress(0, 0)
        return {"slug": slug, "pages": 0, "built": 0,
                "skipped": 0, "errors": 0, "total_fences": 0}
    sem = asyncio.Semaphore(domains.dd.synth.params.BACKFILL_CONCURRENCY)
    total = len(page_keys)
    done = 0

    async def _one(k: str) -> tuple[str, int, str]:
        nonlocal done
        result = await _backfill_one(slug, k, sem)
        done += 1
        if on_progress:
            await on_progress(done, total)
        return result

    results = await asyncio.gather(*(_one(k) for k in page_keys))
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
    """Normalize one page in place; returns (page_slug, status ∈ {'normalized','unchanged','error'})."""
    async with sem:
        parsed = domain.parse_page_key(page_key_str)
        if parsed is None:
            return (page_key_str, "error")
        idx, page_slug = parsed
        try:
            s = domains.dd.ingestion.storage.service.get_storage()
            body = await s.read_text(page_key_str)
            normalized = domains.dd.synth.nodes.corpus_normalize.domain.normalize_doc(body).body
            changed = normalized != body
            # Raw always preserved; cheap idempotent overwrite on subsequent runs.
            raw_k = domains.dd.ingestion.storage.keys.raw_page_key(slug, idx, page_slug)
            await s.write(raw_k, body, content_type = "text/markdown")
            if changed:
                await s.write(
                    page_key_str, normalized, content_type = "text/markdown",
                )
            # Vault rebuild on normalized body — existing vault was hashed
            # against raw, so post-normalize it's stale.
            sentinelized, manifest = domains.dd.synth.nodes.vault.domain.build_manifest(
                framework = slug, source_key = page_key_str, md_text = normalized,
            )
            vk = domains.dd.ingestion.storage.keys.vault_manifest_key(slug, idx, page_slug)
            sk = domains.dd.ingestion.storage.keys.vault_sentinelized_key(slug, idx, page_slug)
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
    sem = asyncio.Semaphore(domains.dd.synth.params.BACKFILL_CONCURRENCY)
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
