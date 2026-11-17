"""corpus_load I/O shell — read the finalized, AI-approved Pages corpus for Planner."""
from __future__ import annotations
import domains
from . import domain
from ..... import graphs

import logging
import time
from typing import Any


logger = logging.getLogger(__name__)

SELECTION_STATUSES = frozenset({"selected", "excluded", "needs_review"})


async def corpus_load_run(state: graphs.pipeline.planner.states.PlannerState, blobs: Any) -> dict:
    """Return only explicitly AI-approved page keys; reject old or incomplete selection manifests."""
    slug = state.get("framework_slug")
    if not slug:
        raise ValueError("planner state missing framework_slug")

    started = time.monotonic()
    graphs.service.emit("corpus_load", "start", slug=slug)
    manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)
    if not manifest:
        raise RuntimeError(f"no finalized ingestion for {slug!r} — run ingestion first")

    entries = manifest.get("entries") or []
    selection_is_complete = bool(entries) and all(
        isinstance(entry.get("page_selection"), dict)
        and entry["page_selection"].get("status") in SELECTION_STATUSES
        for entry in entries
    )
    if entries and not selection_is_complete:
        raise RuntimeError(
            f"page selection is incomplete for {slug!r}; re-extract the documentation before running Planner"
        )

    keys: list[str] = []
    byte_sizes: list[int] = []
    excluded = 0
    for idx, entry in enumerate(entries):
        selection = entry.get("page_selection") or {}
        if selection.get("status") != "selected" or selection.get("needs_review"):
            excluded += 1
            continue
        keys.append(
            entry.get("key")
            or domains.dd.ingestion.storage.keys.page_key(slug, idx, entry.get("slug") or "")
        )
        byte_sizes.append(int(entry.get("bytes") or 0))

    if not keys:
        raise RuntimeError(f"no AI-approved Pages are available for Planner for {slug!r}")

    load_ms = graphs.service.elapsed_ms(started)
    stats = domain.build_corpus_stats(
        byte_sizes,
        manifest,
        load_ms,
        excluded_page_selection=excluded,
    )
    domains.dd.pipeline.observability.service.span_attrs("corpus", stats)
    logger.info(
        f"[corpus_load] {slug}: {stats['total_files']} AI-approved files "
        f"(-{excluded} not approved), {stats['total_bytes'] // 1024} KB total, "
        f"p10/p50/p90 = {stats['p10_bytes']}/{stats['median_bytes']}/{stats['p90_bytes']} B, "
        f"load={load_ms}ms"
    )
    graphs.service.emit(
        "corpus_load",
        "done",
        files=stats["total_files"],
        excluded_page_selection=excluded,
        total_bytes=stats["total_bytes"],
        wall_ms=load_ms,
        tier_kind=stats.get("tier_kind"),
    )
    return {"raw_files": keys, "corpus_stats": stats}
