"""I/O wrapper for post-ingest normalize: read bodies, dispatch to
`domain.split_monolith` or `domain.dedup_pages`, write back, swap manifest."""
from __future__ import annotations
import domains
from . import domain, params

import asyncio
import logging



logger = logging.getLogger(__name__)


async def apply_to_store(store: domains.dd.ingestion.storage.service.Store) -> dict:
    """Single-large-entry → split; multi-page → dedup. Rewrites the manifest
    atomically; returns a summary dict for Progress.record_post."""
    current = store.manifest
    input_files = len(current)
    input_bytes = sum(e.bytes for e in current)
    if input_files == 1 and current[0].bytes >= params.MONOLITH_SPLIT_THRESHOLD_BYTES:
        only = current[0]
        try:
            body = await store.read_body(0)
        except Exception as e:
            logger.warning(f"[post] body read failed: {e}")
            return domain.make_summary("split", input_files, input_bytes, current)
        writes, stubs, dupes = domain.split_monolith(body, only.slug)
        if len(writes) == 1 and writes[0][1] == body:
            return domain.make_summary(
                "split", input_files, input_bytes, current, was_split=False,
            )
        await store.delete_body_by_key(only.key)
        new_entries: list[domains.dd.ingestion.storage.entities.ManifestEntry] = []
        write_batch: list = []
        for new_idx, (slug, sec_body, source_path) in enumerate(writes):
            new_key = domains.dd.ingestion.storage.keys.page_key(store.framework_slug, new_idx, slug)
            write_batch.append((new_key, sec_body, "text/markdown"))
            # A changelog-release sub-page (domain._split_changelog_releases)
            # gets its own tier so corpus_load can drop it from the Planner/
            # Synth path — every other split page keeps the parent's tier.
            tier = (
                "changelog"
                if domains.dd.ingestion.post.patterns.CHANGELOG_RELEASE_MARKER in slug
                else only.tier
            )
            new_entries.append(domains.dd.ingestion.storage.entities.ManifestEntry(
                idx = new_idx,
                slug = slug,
                url = only.url,
                tier = tier,
                bytes = len(sec_body.encode("utf-8")),
                title = slug,
                key = new_key,
                source_path = source_path,
            ))
        await store.minio.write_many(write_batch)
        await store.replace_manifest(new_entries)
        return domain.make_summary(
            "split", 
            input_files, 
            input_bytes, 
            new_entries,
            was_split = True, 
            stubs = stubs, 
            dupes = dupes,
        )
    if input_files == 0:
        return domain.make_summary("dedup", 0, 0, [])
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
    expanded: list[tuple[str, str, str, tuple[str, str]]] = []
    n_oversized = 0
    for s, u, b in deduped:
        if len(b.encode("utf-8")) >= params.OVERSIZED_SPLIT_BYTES:
            subs = domain.split_oversized_page(s, b, params.OVERSIZED_SPLIT_BYTES)
            if len(subs) > 1:
                for ns, nb, _sp in subs:
                    expanded.append((ns, u, nb, (s, u)))
                n_oversized += 1
                logger.info(
                    f"[post] oversized pre-split: {s} "
                    f"({len(b.encode('utf-8')) // 1024} KB) → "
                    f"{len(subs)} sections"
                )
                continue
        expanded.append((s, u, b, (s, u)))
    if stubs == 0 and dupes == 0 and n_oversized == 0:
        return domain.make_summary("dedup", input_files, input_bytes, current)
    del_sem = asyncio.BoundedSemaphore(params.DELETE_CONCURRENCY)

    async def _del_one(e):
        async with del_sem:
            await store.delete_body_by_key(e.key)

    await asyncio.gather(*(_del_one(e) for e in current))
    new_entries = []
    write_batch: list = []
    for new_idx, (slug, url, body, parent) in enumerate(expanded):
        pslug, purl = parent
        prev = next(
            (e for e in current if e.url == purl and e.slug == pslug), None,
        )
        tier = prev.tier if prev else (current[0].tier if current else "unknown")
        # Split children (slug != parent slug) take their own heading-derived
        # slug as title, mirroring the monolith split branch; untouched pages
        # keep the parent title.
        title = prev.title if (prev and slug == pslug) else slug
        new_key = domains.dd.ingestion.storage.keys.page_key(store.framework_slug, new_idx, slug)
        write_batch.append((new_key, body, "text/markdown"))
        new_entries.append(domains.dd.ingestion.storage.entities.ManifestEntry(
            idx = new_idx,
            slug = slug,
            url = url,
            tier = tier,
            bytes = len(body.encode("utf-8")),
            title = title,
            key = new_key,
            # Already distinct per page here (unlike the split branch) —
            # mirrored so downstream consumers can always read source_path
            # without an url fallback.
            source_path = url,
            # Tier 2 enrichment survives the rewrite (prev matched on
            # url+slug); Tier 1 entries carry "" and stay "" here.
            section = (prev.section if prev else "") or "",
            notes = (prev.notes if prev else "") or "",
            # Tier 3 sitemap lastmod, same prev-match survival.
            lastmod = (prev.lastmod if prev else "") or "",
        ))
    await store.minio.write_many(write_batch)
    await store.replace_manifest(new_entries)
    return domain.make_summary(
        "dedup", 
        input_files, 
        input_bytes, 
        new_entries,
        was_split = n_oversized > 0, 
        stubs = stubs, 
        dupes = dupes,
    )
