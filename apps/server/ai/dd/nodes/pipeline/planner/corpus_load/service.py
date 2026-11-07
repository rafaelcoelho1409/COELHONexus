"""corpus_load I/O shell — reads the framework's finalized manifest and builds the page-key list and the corpus stats."""
from __future__ import annotations
import domains
from . import domain
from ..... import graphs

import logging
import time
from typing import Any


logger = logging.getLogger(__name__)


async def corpus_load_run(state: graphs.pipeline.planner.states.PlannerState, blobs: Any) -> dict:
    """Inventory the framework's ingested corpus: the page keys that go to the planner and the corpus stats.

    Changelog-release pages, course pages (`/course/`) and blog posts (`/blog/`) are kept in storage (still browsable in the
    ingestion explorer) but excluded here: they are release notes, step-by-step progressions and dated announcements, not study
    material, and each would seed chapters of its own shape ("Module 3", "Qdrant 1.15") and cost its own judge call. The URL tests
    (not the tier) are what let already-ingested manifests gain the rule without re-ingestion."""
    slug = state.get("framework_slug")
    if not slug:
        raise ValueError("planner state missing framework_slug")

    t0 = time.monotonic()
    graphs.service.emit("corpus_load", "start", slug = slug)
    manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)
    if not manifest:
        raise RuntimeError(f"no finalized ingestion for {slug!r} — run ingestion first")

    keys: list[str] = []
    byte_sizes: list[int] = []
    n_changelog = n_course = n_blog = 0
    for idx, entry in enumerate(manifest.get("entries") or []):
        url = entry.get("url") or ""
        if entry.get("tier") == "changelog":
            n_changelog += 1
            continue
        if "/course/" in url:
            n_course += 1
            continue
        if "/blog/" in url:
            n_blog += 1
            continue
        # Entries written by ingestion's finalize step carry explicit keys; older manifests fall back to the derived key shape.
        keys.append(entry.get("key") or domains.dd.ingestion.storage.keys.page_key(slug, idx, entry.get("slug") or ""))
        byte_sizes.append(int(entry.get("bytes") or 0))

    load_ms = graphs.service.elapsed_ms(t0)
    stats = domain.build_corpus_stats(
        byte_sizes, manifest, load_ms,
        excluded_changelog = n_changelog,
        excluded_course = n_course,
        excluded_blog = n_blog,
    )
    domains.dd.pipeline.observability.service.span_attrs("corpus", stats)
    logger.info(
        f"[corpus_load] {slug}: {stats['total_files']} files "
        f"(-{n_changelog} changelog, -{n_course} course, -{n_blog} blog excluded), "
        f"{stats['total_bytes'] // 1024} KB total, p10/p50/p90 = {stats['p10_bytes']}/{stats['median_bytes']}/"
        f"{stats['p90_bytes']} B, load={load_ms}ms"
    )
    graphs.service.emit(
        "corpus_load", "done",
        files = stats["total_files"],
        excluded_changelog = n_changelog,
        excluded_course = n_course,
        excluded_blog = n_blog,
        total_bytes = stats["total_bytes"],
        wall_ms = load_ms,
        tier_kind = stats.get("tier_kind"),
    )
    return {"raw_files": keys, "corpus_stats": stats}
