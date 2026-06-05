"""I/O wrapper for post-ingest normalize: read bodies, dispatch to
`domain.split_monolith` or `domain.dedup_pages`, write back, swap manifest."""
from __future__ import annotations

import asyncio
import logging

from ..storage import ManifestEntry, Store, page_key
from .domain import dedup_pages, make_summary, split_monolith
from .params import MONOLITH_SPLIT_THRESHOLD_BYTES


logger = logging.getLogger(__name__)


# 32 amortizes latency without overwhelming the MinIO pool (serial was 75s/1500 pages).
_READ_CONCURRENCY = 32
_DELETE_CONCURRENCY = 32


async def apply_to_store(store: Store) -> dict:
    """Single-large-entry → split; multi-page → dedup. Rewrites the manifest
    atomically; returns a summary dict for Progress.record_post."""
    current = store.manifest
    input_files = len(current)
    input_bytes = sum(e.bytes for e in current)
    if input_files == 1 and current[0].bytes >= MONOLITH_SPLIT_THRESHOLD_BYTES:
        only = current[0]
        try:
            body = await store.read_body(0)
        except Exception as e:
            logger.warning(f"[post] body read failed: {e}")
            return make_summary("split", input_files, input_bytes, current)
        writes, stubs, dupes = split_monolith(body, only.slug)
        if len(writes) == 1 and writes[0][1] == body:
            return make_summary(
                "split", input_files, input_bytes, current, was_split=False,
            )
        await store.delete_body_by_key(only.key)
        new_entries: list[ManifestEntry] = []
        write_batch: list = []
        for new_idx, (slug, sec_body) in enumerate(writes):
            new_key = page_key(store.framework_slug, new_idx, slug)
            write_batch.append((new_key, sec_body, "text/markdown"))
            new_entries.append(ManifestEntry(
                idx = new_idx, 
                slug = slug, 
                url = only.url, 
                tier = only.tier,
                bytes = len(sec_body.encode("utf-8")),
                title = slug, 
                key = new_key,
            ))
        await store.minio.write_many(write_batch)
        await store.replace_manifest(new_entries)
        return make_summary(
            "split", 
            input_files, 
            input_bytes, 
            new_entries,
            was_split = True, 
            stubs = stubs, 
            dupes = dupes,
        )
    if input_files == 0:
        return make_summary("dedup", 0, 0, [])
    read_sem = asyncio.BoundedSemaphore(_READ_CONCURRENCY)

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
    deduped, stubs, dupes = dedup_pages(raw_pages)
    if stubs == 0 and dupes == 0:
        return make_summary("dedup", input_files, input_bytes, current)
    del_sem = asyncio.BoundedSemaphore(_DELETE_CONCURRENCY)

    async def _del_one(e):
        async with del_sem:
            await store.delete_body_by_key(e.key)

    await asyncio.gather(*(_del_one(e) for e in current))
    new_entries = []
    write_batch: list = []
    for new_idx, (slug, url, body) in enumerate(deduped):
        prev = next(
            (e for e in current if e.url == url and e.slug == slug), None,
        )
        tier = prev.tier if prev else (current[0].tier if current else "unknown")
        title = prev.title if prev else slug
        new_key = page_key(store.framework_slug, new_idx, slug)
        write_batch.append((new_key, body, "text/markdown"))
        new_entries.append(ManifestEntry(
            idx = new_idx, 
            slug = slug, 
            url = url, 
            tier = tier,
            bytes = len(body.encode("utf-8")),
            title = title, 
            key = new_key,
        ))
    await store.minio.write_many(write_batch)
    await store.replace_manifest(new_entries)
    return make_summary(
        "dedup", 
        input_files, 
        input_bytes, 
        new_entries,
        was_split = False, 
        stubs = stubs, 
        dupes = dupes,
    )
