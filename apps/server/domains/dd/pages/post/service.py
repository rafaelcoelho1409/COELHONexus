"""Content-preserving page splitting and legacy debug post-processing."""
from __future__ import annotations
import domains
from . import domain, params

import asyncio
import logging
from dataclasses import replace



logger = logging.getLogger(__name__)



async def prepare_pages(store: domains.dd.pages.storage.service.Store) -> dict:
    """Split bundles and oversized pages without normalizing, filtering, or deduplicating extracted content.

    A Tier 1 bundle is segmented into source pages, and oversized pages are divided at Markdown headings so downstream
    agents can read them without truncation. Each output is still derived directly from the extracted Markdown.
    """
    current = store.manifest
    input_files = len(current)
    input_bytes = sum(entry.bytes for entry in current)

    if input_files == 1 and current[0].bytes >= params.MONOLITH_SPLIT_THRESHOLD_BYTES:
        only = current[0]
        try:
            body = await store.read_body(0)
        except Exception as e:
            logger.warning(f"[post] body read failed before structural splitting: {e}")
            return domain.make_summary(params.SUMMARY_KIND_PREPARE, input_files, input_bytes, current)
        writes, _stubs, _duplicates = await asyncio.to_thread(
            domain.split_monolith,
            body,
            only.slug,
            cleanup = False,
        )
        if not writes:
            writes = [(only.slug, body, only.url or "")]
        if len(writes) == 1 and writes[0][1] == body:
            return domain.make_summary(
                params.SUMMARY_KIND_PREPARE,
                input_files,
                input_bytes,
                current,
            )

        nav_index = getattr(store, "nav_index", None) or []
        ordered = domain.order_by_nav(writes, [url for url, _section in nav_index])
        sections = domain.sections_by_nav(ordered, nav_index)
        if only.key != only.raw_key:
            await store.delete_body_by_key(only.key)
        key_factory = lambda idx, slug: domains.dd.pages.storage.keys.stage_page_key(
            store.framework_slug,
            store.run_id,
            idx,
            slug,
        )
        new_entries, write_batch = domain.split_manifest(
            store.framework_slug,
            only,
            ordered,
            sections,
            key_factory,
        )
        await store.minio.write_many(write_batch)
        await store.replace_manifest(new_entries)
        return domain.make_summary(
            params.SUMMARY_KIND_PREPARE,
            input_files,
            input_bytes,
            new_entries,
            was_split = True,
        )

    if not current:
        return domain.make_summary(params.SUMMARY_KIND_PREPARE, 0, 0, [])

    read_sem = asyncio.BoundedSemaphore(params.READ_CONCURRENCY)

    async def read_one(entry):
        async with read_sem:
            body = await store.read_body_by_key(entry.key)
        return (entry.slug, entry.url, body)

    pages = list(await asyncio.gather(*(read_one(entry) for entry in current)))
    expanded, oversized = domain.presplit_oversized(
        pages,
        params.OVERSIZED_SPLIT_BYTES,
        deduplicate = False,
    )
    if not oversized:
        return domain.make_summary(params.SUMMARY_KIND_PREPARE, input_files, input_bytes, current)

    for slug, size_kb, children in oversized:
        logger.info(f"[post] page-selection input split: {slug} ({size_kb} KB) → {children} pages")

    delete_sem = asyncio.BoundedSemaphore(params.DELETE_CONCURRENCY)

    async def delete_one(entry):
        async with delete_sem:
            if entry.key != entry.raw_key:
                await store.delete_body_by_key(entry.key)

    await asyncio.gather(*(delete_one(entry) for entry in current))
    key_factory = lambda idx, slug: domains.dd.pages.storage.keys.stage_page_key(
        store.framework_slug,
        store.run_id,
        idx,
        slug,
    )
    new_entries, write_batch = domain.dedup_manifest(
        store.framework_slug,
        current,
        expanded,
        key_factory,
    )
    await store.minio.write_many(write_batch)
    await store.replace_manifest(new_entries)
    return domain.make_summary(
        params.SUMMARY_KIND_PREPARE,
        input_files,
        input_bytes,
        new_entries,
        was_split = True,
    )


async def publish_extracted_pages(store: domains.dd.pages.storage.service.Store) -> int:
    """Move only structurally split outputs out of staging; untouched pages keep their immutable raw object key."""
    current = store.manifest
    read_sem = asyncio.BoundedSemaphore(params.READ_CONCURRENCY)

    async def publish_one(entry):
        async with read_sem:
            body = await store.read_body_by_key(entry.key)
            if entry.raw_key:
                try:
                    raw_body = await store.read_body_by_key(entry.raw_key)
                except Exception:
                    raw_body = None
                if raw_body == body:
                    return replace(entry, key=entry.raw_key, bytes=len(body.encode("utf-8")))

            key = domains.dd.pages.storage.keys.published_page_key(
                store.framework_slug,
                store.run_id,
                entry.idx,
                entry.slug,
            )
            await domains.dd.pages.storage.service.write_immutable(
                store.minio,
                key,
                body,
                params.PAGE_CONTENT_TYPE,
            )
            return replace(entry, key=key, bytes=len(body.encode("utf-8")))

    published = list(await asyncio.gather(*(publish_one(entry) for entry in current)))
    await store.replace_manifest(published)
    return len(published)


async def apply_to_store(store: domains.dd.pages.storage.service.Store) -> dict:
    """Single-large-entry → split; multi-page → dedup. Rewrites the manifest
    atomically; returns a summary dict for Progress.record_post."""
    current = store.manifest
    # The site's name that every page's `<title>` ends with ("… | Grafana Tempo documentation") is not part of the title.
    for entry, title in zip(current, domain.strip_site_suffix([e.title for e in current])):
        entry.title = title
    input_files = len(current)
    input_bytes = sum(e.bytes for e in current)
    if input_files == 1 and current[0].bytes >= params.MONOLITH_SPLIT_THRESHOLD_BYTES:
        only = current[0]
        try:
            body = await store.read_body(0)
        except Exception as e:
            logger.warning(f"[post] body read failed: {e}")
            return domain.make_summary(params.SUMMARY_KIND_SPLIT, input_files, input_bytes, current)
        # Seconds of pure-Python work on a multi-megabyte bundle: off the event loop, which serves the cancel watcher.
        writes, stubs, dupes = await asyncio.to_thread(domain.split_monolith, body, only.slug)
        if len(writes) == 1 and writes[0][1] == body:
            return domain.make_summary(
                params.SUMMARY_KIND_SPLIT, input_files, input_bytes, current, was_split=False,
            )
        # The site's own page order and sections (its llms.txt), when Tier 1 found one — the bundle is often
        # alphabetical by URL, and its URL folders say little (Claude Code's are all `docs/en`).
        nav_index = getattr(store, "nav_index", None) or []
        ordered = domain.order_by_nav(writes, [url for url, _section in nav_index])
        if ordered != writes:
            logger.info(f"[post] {store.framework_slug}: {len(ordered)} pages put in the site's navigation order")
        writes = ordered
        sections = domain.sections_by_nav(writes, nav_index)
        if only.key != only.raw_key:
            await store.delete_body_by_key(only.key)
            await store.delete_vault(only.idx, only.slug)  # the bundle's vault, if one was built, is orphaned by the split
        key_factory = lambda idx, slug: domains.dd.pages.storage.keys.stage_page_key(store.framework_slug, store.run_id, idx, slug)
        new_entries, write_batch = domain.split_manifest(store.framework_slug, only, writes, sections, key_factory)
        await store.minio.write_many(write_batch)
        await store.replace_manifest(new_entries)
        return domain.make_summary(
            params.SUMMARY_KIND_SPLIT,
            input_files,
            input_bytes,
            new_entries,
            was_split = True,
            stubs = stubs,
            dupes = dupes,
        )
    if input_files == 0:
        return domain.make_summary(params.SUMMARY_KIND_DEDUP, 0, 0, [])
    read_sem = asyncio.BoundedSemaphore(params.READ_CONCURRENCY)

    async def _read_one(e):
        async with read_sem:
            try:
                b = await store.read_body_by_key(e.key)
            except Exception:
                b = ""
        return (e.slug, e.url, b)

    raw_pages: list[tuple[str, str, str]] = list(
        await asyncio.gather(*(_read_one(e) for e in current))
    )
    deduped, stubs, dupes = domain.dedup_pages(raw_pages)
    # Oversized pre-split (multi-page corpora only — the monolith branch
    # above owns single-page corpora): individual pages over
    # OVERSIZED_SPLIT_BYTES would otherwise truncate silently at
    # doc_distill/digest's 100KB caps. Same H2/H3 splitter, H1-prepended
    # self-contained children sharing the parent URL; no clean split →
    # page passes through intact, never dropped here.
    expanded, oversized = domain.presplit_oversized(deduped, params.OVERSIZED_SPLIT_BYTES)
    for slug, size_kb, children in oversized:
        logger.info(f"[post] oversized pre-split: {slug} ({size_kb} KB) → {children} sections")
    if stubs == 0 and dupes == 0 and not oversized:
        return domain.make_summary(params.SUMMARY_KIND_DEDUP, input_files, input_bytes, current)
    del_sem = asyncio.BoundedSemaphore(params.DELETE_CONCURRENCY)

    async def _del_one(e):
        async with del_sem:
            if e.key != e.raw_key:
                await store.delete_body_by_key(e.key)
                await store.delete_vault(e.idx, e.slug)  # rewritten under a new idx: the old vault can never be found again

    await asyncio.gather(*(_del_one(e) for e in current))
    key_factory = lambda idx, slug: domains.dd.pages.storage.keys.stage_page_key(store.framework_slug, store.run_id, idx, slug)
    new_entries, write_batch = domain.dedup_manifest(store.framework_slug, current, expanded, key_factory)
    await store.minio.write_many(write_batch)
    await store.replace_manifest(new_entries)
    return domain.make_summary(
        params.SUMMARY_KIND_DEDUP,
        input_files,
        input_bytes,
        new_entries,
        was_split = bool(oversized),
        stubs = stubs,
        dupes = dupes,
    )
