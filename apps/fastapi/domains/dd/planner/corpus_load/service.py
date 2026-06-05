"""corpus_load I/O shell — orchestrates the manifest read, stats build,
and SSE/OTel surface."""
from __future__ import annotations

import logging
import time

from ...ingestion.storage import (
    get_storage,
    page_key,
    read_framework_manifest,
)
from ..observability import attach_span_attrs
from ..progress import emit_progress
from ..state import PlannerState

from .domain import build_corpus_stats


logger = logging.getLogger(__name__)


async def corpus_load_run(state: PlannerState) -> dict:
    """Inventory the framework's ingested corpus. Reads the canonical
    MinIO manifest, builds per-page key list + size stats, emits the
    LangGraph state patch + OTel attrs + SSE events."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    if not slug:
        raise ValueError("planner state missing framework_slug")

    t0 = time.monotonic()
    await emit_progress(thread_id, "corpus_load", "start", slug = slug)
    minio = get_storage()
    manifest = await read_framework_manifest(minio, slug)
    if not manifest:
        raise RuntimeError(
            f"no finalized ingestion for {slug!r} — run ingestion first"
        )

    entries = manifest.get("entries") or []
    keys: list[str] = []
    byte_sizes: list[int] = []
    for idx, entry in enumerate(entries):
        # Manifest entries written by ingestion's finalize step carry
        # explicit MinIO keys; fall back to the derived key shape for
        # older manifests that predate that field.
        k = entry.get("key") or page_key(slug, idx, entry.get("slug") or "")
        keys.append(k)
        byte_sizes.append(int(entry.get("bytes") or 0))

    load_ms = int((time.monotonic() - t0) * 1000)
    stats = build_corpus_stats(byte_sizes, manifest, load_ms)

    attach_span_attrs("corpus", stats)

    n = stats["total_files"]
    logger.info(
        f"[corpus_load] {slug}: {n} files, "
        f"{stats['total_bytes'] // 1024} KB total, "
        f"p10/p50/p90 = {stats['p10_bytes']}/{stats['median_bytes']}/"
        f"{stats['p90_bytes']} B, load={load_ms}ms"
    )
    await emit_progress(
        thread_id, "corpus_load", "done",
        files = n,
        total_bytes = stats["total_bytes"],
        wall_ms = load_ms,
        tier_kind = stats.get("tier_kind"),
    )
    return {"raw_files": keys, "corpus_stats": stats}
