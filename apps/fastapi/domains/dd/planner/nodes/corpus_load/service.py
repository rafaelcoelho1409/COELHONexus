"""corpus_load I/O shell — orchestrates the manifest read, stats build,
and SSE/OTel surface."""
from __future__ import annotations
import domains
from . import domain

import logging
import time


logger = logging.getLogger(__name__)


async def corpus_load_run(state: domains.dd.planner.state.PlannerState) -> dict:
    """Inventory the framework's ingested corpus. Reads the canonical
    MinIO manifest, builds per-page key list + size stats, emits the
    LangGraph state patch + OTel attrs + SSE events."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    if not slug:
        raise ValueError("planner state missing framework_slug")

    t0 = time.monotonic()
    await domains.dd.planner.runtime.progress.service.emit_progress(thread_id, "corpus_load", "start", slug = slug)
    minio = domains.dd.ingestion.storage.service.get_storage()
    manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
    if not manifest:
        raise RuntimeError(
            f"no finalized ingestion for {slug!r} — run ingestion first"
        )

    entries = manifest.get("entries") or []
    keys: list[str] = []
    byte_sizes: list[int] = []
    n_changelog = 0
    n_course = 0
    n_blog = 0
    for idx, entry in enumerate(entries):
        # Changelog-release pages (ingestion.post.domain._split_changelog_releases)
        # are release-notes noise, not study material — dozens of them would
        # otherwise pollute chapter_propose's heading seeds and each cost its
        # own off_topic LLM judge call for near-zero signal. Kept in MinIO
        # (still browsable in the ingestion explorer) but excluded here.
        if entry.get("tier") == "changelog":
            n_changelog += 1
            continue
        # Qdrant Academy course pages (/course/…): pedagogical step-by-step
        # progression, not reference — mixing them into chapter_propose's seeds
        # yields "Module 3"-shaped chapters competing with real reference
        # chapters. Same kept-in-MinIO/browsable, excluded-from-Planner shape
        # as changelog above. URL-substring gate (not tier) so already-
        # ingested manifests gain it without re-ingestion; Tier 1 bundle URLs
        # (llms-full.txt) never contain "/course/", so Tier 1 is unaffected.
        if "/course/" in (entry.get("url") or ""):
            n_course += 1
            continue
        # Blog posts (/blog/…): release announcements and partnership news —
        # dates quickly, seeds "Qdrant 1.15"-shaped chapters. Technical
        # Articles (/articles/…) are deliberately KEPT: durable explainers
        # (quantization methods, benchmarks) that teach concepts. Same
        # kept-browsable/excluded-from-Planner shape and Tier-1 safety as
        # /course/ above.
        if "/blog/" in (entry.get("url") or ""):
            n_blog += 1
            continue
        # Manifest entries written by ingestion's finalize step carry
        # explicit MinIO keys; fall back to the derived key shape for
        # older manifests that predate that field.
        k = entry.get("key") or domains.dd.ingestion.storage.keys.page_key(slug, idx, entry.get("slug") or "")
        keys.append(k)
        byte_sizes.append(int(entry.get("bytes") or 0))

    load_ms = int((time.monotonic() - t0) * 1000)
    stats = domain.build_corpus_stats(
        byte_sizes, manifest, load_ms,
        excluded_changelog = n_changelog,
        excluded_course = n_course,
        excluded_blog = n_blog,
    )

    domains.dd.planner.runtime.observability.service.attach_span_attrs("corpus", stats)

    n = stats["total_files"]
    logger.info(
        f"[corpus_load] {slug}: {n} files "
        f"(-{n_changelog} changelog, -{n_course} course, -{n_blog} blog excluded), "
        f"{stats['total_bytes'] // 1024} KB total, "
        f"p10/p50/p90 = {stats['p10_bytes']}/{stats['median_bytes']}/"
        f"{stats['p90_bytes']} B, load={load_ms}ms"
    )
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "corpus_load", "done",
        files = n,
        excluded_changelog = n_changelog,
        excluded_course = n_course,
        excluded_blog = n_blog,
        total_bytes = stats["total_bytes"],
        wall_ms = load_ms,
        tier_kind = stats.get("tier_kind"),
    )
    return {"raw_files": keys, "corpus_stats": stats}
