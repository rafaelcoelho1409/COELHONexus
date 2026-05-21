"""Substep 1 — corpus_load: inventory the framework's ingested corpus.

Reads the canonical MinIO manifest for `framework_slug` and produces:

  state.raw_files     — list of MinIO keys (one per page). Just pointers;
                        the actual page bodies stay in MinIO and are
                        loaded on demand by downstream substeps that need
                        them (off_topic for embeddings, map for labeling).
                        Keys-only keeps the LangGraph checkpoint cheap —
                        carrying 1500 markdown bodies (~10 MB) through
                        every super-step would blow up Postgres writes.

  state.corpus_stats  — observability dict: file count, total bytes,
                        size distribution percentiles (p10/p50/p90),
                        load time. Mirrors the v1 PlannerProgress
                        `record_corpus_load()` fields. Consumed by the
                        FastHTML substep card AND attached as OTel span
                        attributes for the LangFuse trace.

Raises if the manifest is missing — planner can't run on an un-ingested
framework. The HTTP layer wraps this as a 503 so the caller knows to
ingest first.
"""
from __future__ import annotations

import logging
import time

from services.docs_distiller.ingestion.storage_minio import (
    get_storage,
    page_key,
)
from services.docs_distiller.ingestion.store import read_framework_manifest
from services.llm.otel_setup import get_tracer

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState


logger = logging.getLogger(__name__)


def _percentile(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile on a pre-sorted list. p ∈ [0, 100]."""
    if not sorted_values:
        return 0
    n = len(sorted_values)
    idx = min(n - 1, max(0, int(round(p / 100.0 * (n - 1)))))
    return sorted_values[idx]


@traced("corpus_load")
async def corpus_load(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    if not slug:
        raise ValueError("planner state missing framework_slug")

    t0 = time.monotonic()
    await emit_progress(thread_id, "corpus_load", "start", slug=slug)
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

    byte_sizes.sort()
    total_bytes = sum(byte_sizes)
    n = len(byte_sizes)
    stats = {
        "total_files":  n,
        "total_bytes":  total_bytes,
        "min_bytes":    byte_sizes[0]  if n else 0,
        "max_bytes":    byte_sizes[-1] if n else 0,
        "p10_bytes":    _percentile(byte_sizes, 10),
        "median_bytes": _percentile(byte_sizes, 50),
        "p90_bytes":    _percentile(byte_sizes, 90),
        "load_ms":      int((time.monotonic() - t0) * 1000),
        "tier_kind":    manifest.get("tier_kind"),
        "ingested_at":  manifest.get("ingested_at"),
    }

    # Attach to the @traced OTel span so LangFuse + Alloy see the metrics
    # tree under the planner/corpus_load span. The decorator already
    # opened the span; we just decorate it with extra attributes.
    tracer = get_tracer()
    if tracer is not None:
        span = tracer.get_current_span() if hasattr(tracer, "get_current_span") else None
        # opentelemetry returns the active span via opentelemetry.trace.get_current_span()
        try:
            from opentelemetry import trace as _otel_trace
            span = _otel_trace.get_current_span()
            for k_, v in stats.items():
                if v is None:
                    continue
                span.set_attribute(f"corpus.{k_}", v)
        except Exception:
            pass

    logger.info(
        f"[corpus_load] {slug}: {n} files, "
        f"{total_bytes // 1024} KB total, "
        f"p10/p50/p90 = {stats['p10_bytes']}/{stats['median_bytes']}/{stats['p90_bytes']} B, "
        f"load={stats['load_ms']}ms"
    )
    await emit_progress(
        thread_id, "corpus_load", "done",
        files=n, total_bytes=total_bytes, wall_ms=stats["load_ms"],
        tier_kind=stats.get("tier_kind"),
    )
    return {"raw_files": keys, "corpus_stats": stats}
