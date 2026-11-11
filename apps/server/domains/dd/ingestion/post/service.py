"""Structural page preparation for Agentic AI, plus the legacy debug post-process path."""
from __future__ import annotations
import domains
from . import domain, params

import asyncio
import logging



logger = logging.getLogger(__name__)


async def _drop_pages_empty_after_normalization(pages, *, body_index: int):
    """Drop only pages whose deterministic corpus cleanup leaves no readable content."""
    if not pages:
        return pages, 0
    normalized = await asyncio.gather(*(
        asyncio.to_thread(
            domains.dd.ingestion.corpus_normalize.domain.normalize_doc,
            page[body_index],
        )
        for page in pages
    ))
    kept = [page for page, result in zip(pages, normalized) if result.body.strip()]
    return kept, len(pages) - len(kept)


async def prepare_for_agentic(store: domains.dd.ingestion.storage.service.Store) -> dict:
    """Create page-sized AI inputs while keeping every extracted page for Agentic AI to assess and normalize.

    Only structural splitting remains deterministic: a Tier 1 bundle is segmented into source pages, and oversized
    pages are divided at Markdown headings so they fit the page auditor's input limit. Stub filtering and duplicate
    removal stay out of the ingestion path; the raw capture remains immutable in MinIO in either case.
    """
    current = store.manifest
    input_files = len(current)
    input_bytes = sum(entry.bytes for entry in current)

    if input_files == 1 and current[0].bytes >= params.MONOLITH_SPLIT_THRESHOLD_BYTES:
        only = current[0]
        try:
            body = await store.read_body(0)
        except Exception as e:
            logger.warning(f"[post] body read failed before Agentic AI: {e}")
            return domain.make_summary(params.SUMMARY_KIND_PREPARE, input_files, input_bytes, current)
        writes, _stubs, _duplicates = await asyncio.to_thread(
            domain.split_monolith,
            body,
            only.slug,
            cleanup = False,
        )
        writes, empty_pages_dropped = await _drop_pages_empty_after_normalization(
            writes,
            body_index=1,
        )
        if not writes:
            await store.delete_body_by_key(only.key)
            await store.delete_vault(only.idx, only.slug)
            await store.replace_manifest([])
            return domain.make_summary(
                params.SUMMARY_KIND_PREPARE,
                input_files,
                input_bytes,
                [],
                was_split=True,
                empty_pages_dropped=empty_pages_dropped,
            )
        if len(writes) == 1 and writes[0][1] == body:
            return domain.make_summary(
                params.SUMMARY_KIND_PREPARE,
                input_files,
                input_bytes,
                current,
                empty_pages_dropped=empty_pages_dropped,
            )

        nav_index = getattr(store, "nav_index", None) or []
        ordered = domain.order_by_nav(writes, [url for url, _section in nav_index])
        sections = domain.sections_by_nav(ordered, nav_index)
        await store.delete_body_by_key(only.key)
        await store.delete_vault(only.idx, only.slug)
        key_factory = lambda idx, slug: domains.dd.ingestion.storage.keys.stage_page_key(
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
            empty_pages_dropped=empty_pages_dropped,
        )

    if not current:
        return domain.make_summary(params.SUMMARY_KIND_PREPARE, 0, 0, [])

    read_sem = asyncio.BoundedSemaphore(params.READ_CONCURRENCY)

    async def read_one(entry):
        async with read_sem:
            body = await store.read_body_by_key(entry.key)
        return (entry.slug, entry.url, body)

    pages = list(await asyncio.gather(*(read_one(entry) for entry in current)))
    pages, empty_pages_dropped = await _drop_pages_empty_after_normalization(
        pages,
        body_index=2,
    )
    expanded, oversized = domain.presplit_oversized(
        pages,
        params.OVERSIZED_SPLIT_BYTES,
        deduplicate = False,
    )
    if not oversized and not empty_pages_dropped:
        return domain.make_summary(params.SUMMARY_KIND_PREPARE, input_files, input_bytes, current)

    for slug, size_kb, children in oversized:
        logger.info(f"[post] Agentic input split: {slug} ({size_kb} KB) → {children} pages")

    delete_sem = asyncio.BoundedSemaphore(params.DELETE_CONCURRENCY)

    async def delete_one(entry):
        async with delete_sem:
            await store.delete_body_by_key(entry.key)
            await store.delete_vault(entry.idx, entry.slug)

    await asyncio.gather(*(delete_one(entry) for entry in current))
    key_factory = lambda idx, slug: domains.dd.ingestion.storage.keys.stage_page_key(
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
        empty_pages_dropped=empty_pages_dropped,
    )


async def apply_to_store(store: domains.dd.ingestion.storage.service.Store) -> dict:
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
        await store.delete_body_by_key(only.key)
        await store.delete_vault(only.idx, only.slug)  # the bundle's vault, if one was built, is orphaned by the split
        key_factory = lambda idx, slug: domains.dd.ingestion.storage.keys.stage_page_key(store.framework_slug, store.run_id, idx, slug)
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
            await store.delete_body_by_key(e.key)
            await store.delete_vault(e.idx, e.slug)  # rewritten under a new idx: the old vault can never be found again

    await asyncio.gather(*(_del_one(e) for e in current))
    key_factory = lambda idx, slug: domains.dd.ingestion.storage.keys.stage_page_key(store.framework_slug, store.run_id, idx, slug)
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
