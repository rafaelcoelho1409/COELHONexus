"""
Knowledge Distiller — per-node observability pages (2026-05-15).

Goal: make every KD node + sub-step inspectable in real time so we stop
debugging 2h Celery studies as a black box. Companion to
`docs/KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md`.

Build order from that doc:
  1. Resolver + Ingestion        ← this file (Stage 1)
  2. Planner (MAP / REDUCE)      → future page
  3. Canary synth                → future page
  4. Synthesize chapter (Phase A/A.5/B/C/D + Self-Refine) → future page
  5–8. Curator / Critic / Assembler / Bandit → future pages

This module exposes:
  - IngestionObservabilityPage(study_id): full-page shell
  - IngestionObservabilityFragment(payload): HTMX fragment, polled every 2s

The data shape comes from FastAPI's
`GET /api/v1/knowledge/studies/{id}/observability/ingestion`. See
`apps/fastapi/routers/v1/knowledge/distiller.py::get_ingestion_observability`.
"""
from fasthtml.common import (
    A, Code, Div, H1, H2, I, Pre, Section, Span, Table, Tbody, Td, Th, Thead, Tr,
)

from components.base import Page


# =============================================================================
# Status chip helpers
# =============================================================================
_STATUS_BADGE_CLASS = {
    "success":         "badge badge-success badge-xs",
    "cache_restored":  "badge badge-info badge-xs",
    "http_error":      "badge badge-error badge-xs",
    "fetch_error":     "badge badge-error badge-xs",
    "timeout":         "badge badge-error badge-xs",
    "extract_empty":   "badge badge-warning badge-xs",
    "downgrade":       "badge badge-warning badge-xs",
    "idle":            "badge badge-ghost badge-xs",
    "running":         "badge badge-info badge-xs",
    "done":            "badge badge-success badge-xs",
    "failed":          "badge badge-error badge-xs",
}


def _status_chip(status: str | None) -> Span:
    if not status:
        return Span("—", cls="badge badge-ghost badge-xs")
    cls = _STATUS_BADGE_CLASS.get(status, "badge badge-ghost badge-xs")
    return Span(status, cls=cls)


def _fmt_ms(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def _fmt_bytes(v) -> str:
    if v is None:
        return "—"
    try:
        n = int(v)
        if n >= 1_048_576:
            return f"{n / 1_048_576:.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n} B"
    except Exception:
        return str(v)


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


# =============================================================================
# Shell page
# =============================================================================
def IngestionObservabilityPage(study_id: str):
    """Full page shell. HTMX fragment polls every 2s to refresh content."""
    return Page(
        f"Ingestion · {study_id[:8]} · KD",
        Section(
            Div(
                # Breadcrumb back to the study detail page
                Div(
                    A(
                        I(data_lucide="arrow-left", cls="w-4 h-4"),
                        Span("Back to study"),
                        href=f"/kd/studies/{study_id}",
                        cls="link link-hover text-xs flex items-center gap-1 mb-2",
                    ),
                ),
                # Title + node nav (future-proofed for the other 7 stages)
                Div(
                    Div(
                        H1("Ingestion observability",
                           cls="text-2xl font-bold"),
                        Span(
                            "Stage 1 of 8 · Resolver + Ingestion",
                            cls="text-xs opacity-60",
                        ),
                        cls="flex-1",
                    ),
                    Div(
                        Span("Refresh: 2s", cls="text-xs opacity-50"),
                        cls="text-right",
                    ),
                    cls="flex items-start justify-between gap-4 mb-4",
                ),
                # Live fragment — polled every 2s. Uses idiomorph so any
                # client-side state (sort, filter) survives swaps when we
                # add those controls later.
                Div(
                    Div(
                        I(data_lucide="loader",
                          cls="w-5 h-5 animate-spin opacity-60"),
                        Span("Waiting for ingestion to emit first record…",
                             cls="text-sm opacity-60 ml-2"),
                        cls="flex items-center gap-2 p-6",
                    ),
                    id="kd-obs-ingestion",
                    hx_get=f"/api/kd/studies/{study_id}/observability/ingestion/fragment",
                    hx_trigger="load, every 2s",
                    hx_ext="morph",
                    hx_swap="morph",
                ),
                cls="max-w-7xl mx-auto px-6 py-6",
            ),
            cls="min-h-screen",
        ),
        active_nav="kd-studies",
    )


# =============================================================================
# Fragment — header + summary + per-URL table
# =============================================================================
def IngestionObservabilityFragment(payload: dict):
    """Live fragment for the ingestion observability page."""
    header = payload.get("header") or {}
    urls = payload.get("urls") or []
    summary = payload.get("summary") or {}
    post_ingest = payload.get("post_ingest")  # may be None

    return Div(
        _HeaderCard(header, summary, post_ingest),
        _PostIngestCard(post_ingest),
        _PerURLTable(urls),
    )


def _HeaderCard(header: dict, summary: dict, post_ingest: dict | None = None) -> Div:
    """Top card: tier, progress bar, summary widgets."""
    tier = header.get("tier") or "—"
    current = header.get("current") or 0
    total = header.get("total") or 0
    status = header.get("status") or "idle"
    pct = (100 * current / total) if total else 0

    by_status = summary.get("by_status") or {}
    by_tier = summary.get("by_tier") or {}

    total_recorded = summary.get("total_recorded") or 0
    success = by_status.get("success") or 0
    cache_restored = by_status.get("cache_restored") or 0
    # "good outcomes" = real fetches + cache hits; what % of records ended
    # up with usable content (vs error/empty)
    good_count = success + cache_restored
    success_pct = (100 * good_count / total_recorded) if total_recorded else 0
    error_count = sum(v for k, v in by_status.items()
                      if k in ("http_error", "fetch_error", "timeout"))
    empty_count = by_status.get("extract_empty") or 0

    median_ms = summary.get("median_fetch_ms")
    p95_ms = summary.get("p95_fetch_ms")
    total_bytes = summary.get("total_bytes") or 0
    total_extracted = summary.get("total_extracted_chars") or 0

    # Files actually present in MinIO after the post-ingest split step.
    # On Tier 1 (monolith) this is typically 100×–1000× the URL-fetch
    # count; on Tier 3/4 it matches `total_recorded`. Falling back to
    # `total_recorded` when post_ingest hasn't been written yet (it's
    # emitted once at end of ingestion stage).
    if post_ingest:
        files_in_minio = post_ingest.get("output_files") or 0
        expansion_ratio = post_ingest.get("expansion_ratio") or 0.0
    else:
        files_in_minio = total_recorded
        expansion_ratio = 1.0 if total_recorded else 0.0

    return Div(
        # Tier + progress
        Div(
            Div(
                Div("Tier", cls="text-xs opacity-50"),
                Div(
                    Span(tier, cls="text-sm font-mono"),
                    _status_chip(status),
                    cls="flex items-center gap-2",
                ),
            ),
            Div(
                Div(
                    Span(f"{current:,} / {total:,}",
                         cls="text-sm font-mono"),
                    Span(f"({pct:.1f}%)", cls="text-xs opacity-60 ml-1"),
                    cls="flex items-baseline justify-between",
                ),
                # DaisyUI progress bar — value tracks `current/total`
                Div(
                    Div(
                        style=f"width: {pct:.1f}%",
                        cls="bg-primary h-2 rounded-full transition-all",
                    ),
                    cls="w-full bg-base-300 h-2 rounded-full overflow-hidden mt-1",
                ),
                cls="flex-1",
            ),
            cls="grid grid-cols-1 md:grid-cols-[160px_1fr] gap-4 items-start",
        ),
        # Stat grid
        Div(
            _Stat("URLs fetched", f"{total_recorded:,}",
                  "input to ingestion"),
            _Stat("Files in MinIO", f"{files_in_minio:,}",
                  (f"×{expansion_ratio:.1f} after split"
                   if post_ingest and post_ingest.get("was_split")
                   else "1:1 with URLs")),
            _Stat("Live fetch",
                  f"{success:,}",
                  "from upstream"),
            _Stat("Cache restored",
                  f"{cache_restored:,}",
                  "from _cache/ingestion/" if cache_restored else None),
            _Stat("Errors", f"{error_count:,}",
                  "http+fetch+timeout"),
            _Stat("Empty extract", f"{empty_count:,}", None),
            _Stat("Median fetch",
                  _fmt_ms(median_ms) + " ms" if median_ms is not None else "—",
                  "live-fetch only"),
            _Stat("Bytes",
                  _fmt_bytes(total_bytes),
                  f"good rate {success_pct:.1f}%" if total_recorded else None),
            cls="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4",
        ),
        # By-tier breakdown
        Div(
            Div("By tier:", cls="text-xs opacity-50 mb-1"),
            Div(
                *[
                    Span(
                        Span(t, cls="font-mono"),
                        " ",
                        Span(f"{n:,}", cls="font-semibold"),
                        cls="text-xs px-2 py-1 bg-base-200 rounded mr-2 mb-1 inline-block",
                    )
                    for t, n in sorted(by_tier.items(),
                                       key=lambda kv: -kv[1])
                ] or [Span("none", cls="text-xs opacity-50")],
                cls="flex flex-wrap",
            ),
            cls="mt-4 pt-3 border-t border-base-300",
        ),
        cls="bg-base-100 border border-base-300 rounded-lg p-5",
    )


def _PostIngestCard(post_ingest: dict | None) -> Div:
    """
    Surfaces the `post_ingest.split_monolith_if_needed` step. Shows the
    multiplier between URLs fetched and files materialized in MinIO so the
    operator's view matches reality on Tier 1 monolith ingests.

    Returns an empty Div when the step hasn't run yet (ingestion still in
    flight) or didn't apply (Tier 3/4 multi-file manifests pass through
    unchanged — in that case input_files == output_files and was_split=False).
    """
    if not post_ingest:
        # Stage hasn't fired yet — render a lightweight placeholder so the
        # operator knows it's expected, not missing.
        return Div(
            Div(
                Span(
                    Span("Post-ingest normalization",
                         cls="text-sm font-semibold"),
                    Span(" · pending",
                         cls="text-xs opacity-50 ml-2"),
                ),
                Span(
                    "Runs after the URL fetch phase completes. Surfaces the "
                    "monolith-split expansion (Tier 1) or pass-through (Tier 3/4).",
                    cls="text-xs opacity-60 block mt-1",
                ),
                cls="px-4 py-3",
            ),
            cls="mt-4 bg-base-100 border border-base-300 border-dashed rounded-lg",
        )

    input_files = post_ingest.get("input_files") or 0
    input_bytes = post_ingest.get("input_bytes") or 0
    output_files = post_ingest.get("output_files") or 0
    output_bytes = post_ingest.get("output_bytes") or 0
    ratio = post_ingest.get("expansion_ratio") or 0.0
    was_split = bool(post_ingest.get("was_split"))
    notes = post_ingest.get("notes") or ""

    return Div(
        Div(
            Span("Post-ingest normalization",
                 cls="text-sm font-semibold"),
            Span(
                " · split applied" if was_split else " · pass-through",
                cls=("text-xs ml-2 " +
                     ("text-success" if was_split else "opacity-60")),
            ),
            cls="px-4 py-3 border-b border-base-300",
        ),
        Div(
            _Stat("Input files", f"{input_files:,}",
                  _fmt_bytes(input_bytes)),
            _Stat("Output files", f"{output_files:,}",
                  _fmt_bytes(output_bytes)),
            _Stat("Expansion", f"×{ratio:.1f}",
                  "split on H1/H2" if was_split else "no split"),
            _Stat("Bytes preserved",
                  f"{(100 * output_bytes / input_bytes):.1f}%"
                  if input_bytes else "—",
                  "post-split vs pre-split"),
            cls="grid grid-cols-2 md:grid-cols-4 gap-3 p-4",
        ),
        Div(
            Span(notes, cls="text-xs opacity-60"),
            cls="px-4 pb-3",
        ) if notes else "",
        cls="mt-4 bg-base-100 border border-base-300 rounded-lg",
    )


def _Stat(label: str, value: str, sub: str | None) -> Div:
    return Div(
        Div(label, cls="text-xs opacity-50"),
        Div(value, cls="text-lg font-semibold leading-tight"),
        Div(sub, cls="text-xs opacity-60") if sub else "",
        cls="bg-base-200 rounded p-3",
    )


# =============================================================================
# STAGE 2 — Planner observability
# =============================================================================
# Companion to Stage 1 (Ingestion) above. Reads the per-substep snapshot
# from FastAPI's GET /studies/{id}/observability/planner. Same poll cadence
# (2 s) and same Page() shell pattern.
#
# Per `docs/KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md` the planner has ~14 sub-
# steps. We render one card per sub-step with the score/decision boundary
# made explicit, so operators can spot mis-decisions in real time without
# reading the synthesized chapter output.
# =============================================================================
def PlannerObservabilityPage(study_id: str):
    """Full page shell. HTMX fragment polls every 2s."""
    return Page(
        f"Planner · {study_id[:8]} · KD",
        Section(
            Div(
                Div(
                    A(
                        I(data_lucide="arrow-left", cls="w-4 h-4"),
                        Span("Back to study"),
                        href=f"/kd/studies/{study_id}",
                        cls="link link-hover text-xs flex items-center gap-1 mb-2",
                    ),
                ),
                Div(
                    Div(
                        H1("Planner observability", cls="text-2xl font-bold"),
                        Span(
                            "Stage 2 of 8 · MAP-REDUCE planner with deterministic clustering",
                            cls="text-xs opacity-60",
                        ),
                        cls="flex-1",
                    ),
                    Div(
                        Span("Refresh: 2s", cls="text-xs opacity-50"),
                        cls="text-right",
                    ),
                    cls="flex items-start justify-between gap-4 mb-4",
                ),
                Div(
                    Div(
                        I(data_lucide="loader",
                          cls="w-5 h-5 animate-spin opacity-60"),
                        Span("Loading planner state…",
                             cls="text-sm opacity-60 ml-2"),
                        cls="flex items-center gap-2 p-6",
                    ),
                    id="kd-obs-planner",
                    hx_get=f"/api/kd/studies/{study_id}/observability/planner/fragment",
                    hx_trigger="load, every 2s",
                    hx_ext="morph",
                    hx_swap="morph",
                ),
                cls="max-w-7xl mx-auto px-6 py-6",
            ),
            cls="min-h-screen",
        ),
        active_nav="kd-studies",
    )


def PlannerObservabilityFragment(payload: dict) -> Div:
    """
    Live fragment for the planner observability page. Each sub-step is
    its own card so the page renders progressively as data arrives.
    """
    study_id = payload.get("study_id") or ""
    status = payload.get("status") or {}
    corpus = payload.get("corpus_load")
    off_topic = payload.get("off_topic")
    dedup = payload.get("dedup")
    cache = payload.get("cache")
    shards = payload.get("shards")
    shard_results = payload.get("shard_results") or []
    reduce_embed = payload.get("reduce_embed")
    reduce_umap = payload.get("reduce_umap")
    reduce_k = payload.get("reduce_k")
    reduce_kmeans = payload.get("reduce_kmeans")
    reduce_thin = payload.get("reduce_thin")
    reduce_split = payload.get("reduce_split")
    chapter_coherence = payload.get("chapter_coherence")
    validation = payload.get("validation")
    coverage = payload.get("coverage")

    return Div(
        _PlannerHeaderCard(status, len(shard_results)),
        _CorpusLoadCard(corpus),
        _OffTopicCard(off_topic),
        _DedupCard(dedup),
        _CacheCard(cache),
        _ShardsCard(shards, shard_results, study_id),
        _ReduceCard(reduce_embed, reduce_umap, reduce_k, reduce_kmeans,
                    reduce_thin, reduce_split),
        _ChapterCoherenceCard(chapter_coherence),
        _ValidationCard(validation, coverage),
    )


def _PlannerHeaderCard(status: dict, n_shards: int) -> Div:
    """Top card: current phase + cumulative elapsed."""
    phase = status.get("phase") or "init"
    elapsed_ms = status.get("elapsed_ms") or 0
    error_msg = status.get("error_msg")

    phase_class = "badge badge-info badge-sm"
    if phase == "done":
        phase_class = "badge badge-success badge-sm"
    elif phase == "failed":
        phase_class = "badge badge-error badge-sm"

    return Div(
        Div(
            Div(
                Div("Current phase", cls="text-xs opacity-50"),
                Div(
                    Span(phase, cls=phase_class),
                    cls="mt-1",
                ),
            ),
            Div(
                Div("Elapsed", cls="text-xs opacity-50"),
                Div(
                    f"{elapsed_ms / 1000:.1f} s"
                    if elapsed_ms < 60000 else f"{elapsed_ms / 60000:.1f} min",
                    cls="text-sm font-mono",
                ),
            ),
            Div(
                Div("Shards observed", cls="text-xs opacity-50"),
                Div(f"{n_shards:,}", cls="text-sm font-mono"),
            ),
            cls="grid grid-cols-3 gap-4",
        ),
        Div(
            Span(error_msg, cls="text-xs text-error"),
            cls="mt-2",
        ) if error_msg else "",
        cls="bg-base-100 border border-base-300 rounded-lg p-5",
    )


def _SubstepCard(title: str, subtitle: str | None, body, status: str = "ready") -> Div:
    """
    Wrapper for each sub-step card. status: "ready" | "pending" | "skipped".
    Pending = no data yet (dashed border). Ready = green underline header.
    """
    border_cls = (
        "border-dashed border-base-300" if status == "pending"
        else "border-base-300"
    )
    return Div(
        Div(
            Span(title, cls="text-sm font-semibold"),
            Span(
                f" · {subtitle}" if subtitle else "",
                cls="text-xs opacity-60",
            ),
            cls="px-4 py-3 border-b border-base-300",
        ),
        Div(body, cls="p-4") if status != "pending" else Div(
            Span("waiting for data…", cls="text-xs opacity-50"),
            cls="p-4 text-center",
        ),
        cls=f"mt-4 bg-base-100 border {border_cls} rounded-lg",
    )


def _CorpusLoadCard(d: dict | None) -> Div:
    if not d:
        return _SubstepCard("2.1 Corpus load", "MinIO read", None, "pending")
    load_ms = d.get("load_ms") or 0
    total_files = d.get("total_files") or 0
    rate = (total_files / max(1, load_ms) * 1000) if load_ms else 0
    return _SubstepCard(
        "2.1 Corpus load",
        f"{load_ms} ms",
        Div(
            _Stat("Files", f"{total_files:,}", None),
            _Stat("Total bytes", _fmt_bytes(d.get("total_bytes") or 0), None),
            _Stat("Min / Median / Max",
                  f"{_fmt_bytes(d.get('min_bytes') or 0)} · "
                  f"{_fmt_bytes(d.get('median_bytes') or 0)} · "
                  f"{_fmt_bytes(d.get('max_bytes') or 0)}",
                  None),
            _Stat("Load time",
                  f"{load_ms:,} ms",
                  f"{rate:.0f} files/s" if load_ms else "—"),
            cls="grid grid-cols-2 md:grid-cols-4 gap-3",
        ),
    )


def _OffTopicCard(d: dict | None) -> Div:
    """
    Per-file cosine table — sortable so the operator can spot boundary
    cases (near-threshold drops, false-positive keeps). Domain coherence
    quantifies how tight the kept set is.
    """
    if not d:
        return _SubstepCard("2.2 Off-topic filter",
                            "embedding cosine vs framework prototype",
                            None, "pending")
    per_file = d.get("per_file_cosines") or []
    threshold = d.get("threshold")
    if threshold is None:
        threshold = 0.30
    # Boundary slugs = within ±0.05 of threshold (false-positive/negative candidates)
    boundary = [
        p for p in per_file
        if abs(p["cosine"] - threshold) <= 0.05
    ]
    # Top 5 dropped + top 5 boundary kept for the table preview
    dropped_top = sorted(
        [p for p in per_file if not p["kept"]],
        key=lambda x: x["cosine"], reverse=True,
    )[:5]
    kept_low = sorted(
        [p for p in per_file if p["kept"]],
        key=lambda x: x["cosine"],
    )[:5]

    return _SubstepCard(
        "2.2 Off-topic filter",
        f"NIM embed · cos vs '{d.get('framework')}' prototype · threshold {threshold:.2f}",
        Div(
            Div(
                _Stat("Kept", f"{d['kept']:,}",
                      f"{d['embedding_provider'] or 'unknown'}"),
                _Stat("Dropped", f"{d['dropped']:,}",
                      f"cos<{threshold:.2f}"),
                _Stat("Domain coherence",
                      f"{d['domain_coherence']:.3f}" if d.get("domain_coherence") is not None else "—",
                      "mean cos of kept→centroid"),
                _Stat("Boundary cases",
                      f"{len(boundary):,}",
                      f"within ±0.05 of threshold"),
                cls="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3",
            ),
            Div(
                Div(
                    Span("Dropped (highest cosines — false-positive drops?)",
                         cls="text-xs opacity-60 font-semibold block mb-1"),
                    Table(
                        Tbody(*[
                            Tr(
                                Td(Code(_truncate(p["slug"], 70),
                                        cls="text-xs font-mono")),
                                Td(Span(f"{p['cosine']:.3f}",
                                        cls="text-xs font-mono text-right text-error")),
                            )
                            for p in dropped_top
                        ]),
                        cls="table table-xs",
                    ),
                    cls="mb-3",
                ) if dropped_top else "",
                Div(
                    Span("Kept (lowest cosines — false-positive keeps?)",
                         cls="text-xs opacity-60 font-semibold block mb-1"),
                    Table(
                        Tbody(*[
                            Tr(
                                Td(Code(_truncate(p["slug"], 70),
                                        cls="text-xs font-mono")),
                                Td(Span(f"{p['cosine']:.3f}",
                                        cls="text-xs font-mono text-right text-warning")),
                            )
                            for p in kept_low
                        ]),
                        cls="table table-xs",
                    ),
                ) if kept_low else "",
            ),
        ),
    )


def _DedupCard(d: dict | None) -> Div:
    if not d:
        return _SubstepCard("2.3 Code-aware dedup",
                            "Jaccard prose + code-set match",
                            None, "pending")
    log = d.get("dedup_log") or []
    threshold = d.get("threshold")
    threshold_str = f"{threshold:.2f}" if threshold is not None else "—"
    elapsed_ms = d.get("elapsed_ms")
    elapsed_str = f"{elapsed_ms} ms" if elapsed_ms is not None else "—"
    return _SubstepCard(
        "2.3 Code-aware dedup",
        f"Jaccard threshold {threshold_str} · {elapsed_str}",
        Div(
            Div(
                _Stat("Pairs checked", f"{d['pairs_checked']:,}", None),
                _Stat("Dropped", f"{d['dropped']:,}",
                      "longer file kept; code sets matched"),
                _Stat("Log entries",
                      f"{len(log):,}",
                      "first 50 shown"),
                cls="grid grid-cols-3 gap-3 mb-3",
            ),
            Div(
                Table(
                    Thead(Tr(
                        Th("Kept", cls="text-xs"),
                        Th("Dropped", cls="text-xs"),
                        Th("Jaccard", cls="text-xs text-right"),
                        Th("Δ bytes", cls="text-xs text-right"),
                    )),
                    Tbody(*[
                        Tr(
                            Td(Code(_truncate(p["slug_kept"], 50),
                                    cls="text-xs font-mono")),
                            Td(Code(_truncate(p["slug_dropped"], 50),
                                    cls="text-xs font-mono opacity-60")),
                            Td(Span(f"{p['jaccard']:.3f}",
                                    cls="text-xs font-mono")),
                            Td(Span(f"{p['len_kept'] - p['len_dropped']:+,}",
                                    cls="text-xs font-mono")),
                        )
                        for p in log[:20]
                    ]),
                    cls="table table-xs table-zebra",
                ),
                cls="overflow-x-auto",
            ) if log else Span("no dups", cls="text-xs opacity-50"),
        ),
    )


def _CacheCard(d: dict | None) -> Div:
    if not d:
        return _SubstepCard("2.5 Plan cache lookup", None, None, "pending")
    hit = bool(d.get("hit"))
    return _SubstepCard(
        "2.5 Plan cache lookup",
        f"{'HIT — skipping MAP/REDUCE' if hit else 'MISS — running full planner'}",
        Div(
            _Stat("Status",
                  "HIT" if hit else "MISS",
                  d.get("cached_at") or "no cached plan"),
            _Stat("Manifest hash",
                  Code((d.get("manifest_hash") or "")[:16] + "…",
                       cls="text-xs font-mono"),
                  "sorted slug list SHA256[:32]"),
            cls="grid grid-cols-2 gap-3",
        ),
    )


def _ShardsCard(shards: dict | None, shard_results: list[dict],
                study_id: str = "") -> Div:
    if not shards:
        return _SubstepCard("2.6 Shard creation + 2.7 MAP",
                            "parallel labelers per shard",
                            None, "pending")
    total = shards.get("total_shards") or 0
    sizes = shards.get("shard_sizes") or []
    completed = len(shard_results)
    pct = (100 * completed / total) if total else 0
    return _SubstepCard(
        "2.6 Shard creation + 2.7 MAP",
        f"{total} shards × ≤{shards.get('shard_size_cap')} files",
        Div(
            Div(
                _Stat("Total shards", f"{total:,}", None),
                _Stat("MAP completed", f"{completed:,}/{total:,}",
                      f"{pct:.0f}%"),
                _Stat("Min / Max size",
                      f"{min(sizes) if sizes else 0} / {max(sizes) if sizes else 0}",
                      None),
                cls="grid grid-cols-3 gap-3 mb-3",
            ),
            Div(
                Span("Waiting for first shard to complete…",
                     cls="text-xs opacity-50"),
                cls="px-3 py-2 bg-base-200 rounded",
            ) if not shard_results else _ShardResultsTable(shard_results, study_id),
        ),
    )


def _ShardResultsTable(shard_results: list[dict], study_id: str = "") -> Div:
    rows = []
    for sr in shard_results:
        idx = sr.get("idx", 0)
        path = sr.get("path", "?")
        path_cls = {
            "strict_json":           "badge badge-success badge-xs",
            "fallback_fc":           "badge badge-warning badge-xs",
            "catchall":              "badge badge-error badge-xs",
            "timeout":               "badge badge-error badge-xs",
            "classical_map":         "badge badge-info badge-xs",
            "global_classical_map":  "badge badge-info badge-xs",
            "llm_map":               "badge badge-success badge-xs",
        }.get(path, "badge badge-ghost badge-xs")
        rows.append(Tr(
            Td(Span(f"#{idx}", cls="text-xs font-mono")),
            Td(Span(path, cls=path_cls)),
            Td(Span(f"{sr.get('n_input_slugs', 0)}", cls="text-xs")),
            Td(Span(f"{sr.get('n_clusters', 0)}", cls="text-xs")),
            Td(Span(f"{sr.get('n_unused', 0)}", cls="text-xs")),
            Td(Span(
                f"{sr.get('elapsed_ms', 0):,} ms"
                if sr.get("elapsed_ms") else "—",
                cls="text-xs font-mono",
            )),
            Td(
                # Replay button — HTMX swap into the row below the table.
                # No-op when study_id wasn't threaded through (defensive).
                Span(
                    I(data_lucide="rotate-cw", cls="w-3 h-3"),
                    "replay",
                    hx_post=(
                        f"/api/kd/studies/{study_id}/observability/planner/replay/{idx}"
                        if study_id else ""
                    ),
                    hx_target="#kd-shard-replay-target",
                    hx_swap="innerHTML",
                    cls="btn btn-xs btn-ghost gap-1 cursor-pointer",
                ) if study_id else Span("—", cls="text-xs opacity-30"),
                cls="text-xs",
            ),
        ))
    return Div(
        Table(
            Thead(Tr(
                Th("#"), Th("Path"), Th("In"), Th("Clusters"),
                Th("Unused"), Th("Elapsed"), Th(""),
            )),
            Tbody(*rows),
            cls="table table-xs table-zebra",
        ),
        # Replay target — gets filled by HTMX with original/replay diff
        Div(id="kd-shard-replay-target", cls="mt-3"),
        cls="overflow-x-auto",
    )


def _ReduceCard(
    embed: dict | None, umap: dict | None, k: dict | None,
    kmeans: dict | None, thin: dict | None, split: dict | None,
) -> Div:
    """
    REDUCE is the deterministic ML chunk: embed → PCA → UMAP →
    k-selection → constrained KMeans → thin-merge → oversize-split.

    Defensive: every value in the Redis snapshot may be None even when the
    parent dict is present (e.g. PCA is skipped on small corpora, leaving
    pca_in_dim/pca_out_dim/pca_explained_variance as explicit None values).
    The render layer guards each None before formatting.
    """
    if not embed and not umap and not k and not kmeans:
        return _SubstepCard("2.9 REDUCE",
                            "Clio pattern: embed → PCA → UMAP → KMeans",
                            None, "pending")

    # ---- PCA + UMAP line (safely render None-able fields) ----
    def _umap_line(u: dict | None) -> str:
        if not u:
            return "pending"
        pca_in = u.get("pca_in_dim")
        pca_out = u.get("pca_out_dim")
        pca_var = u.get("pca_explained_variance")
        if pca_in is not None and pca_out is not None:
            pca_part = (
                f"PCA {pca_in}d→{pca_out}d"
                + (f" (var {pca_var:.3f})" if pca_var is not None else "")
                + " · "
            )
        else:
            pca_part = "PCA skipped (n_clusters ≤ 128) · "
        umap_in = u.get("umap_in_dim")
        umap_out = u.get("umap_out_dim")
        n_neighbors = u.get("n_neighbors")
        min_dist = u.get("min_dist")
        return (
            pca_part
            + f"UMAP {umap_in}d→{umap_out}d "
            + f"(n={n_neighbors}, min_dist={min_dist})"
        )

    # ---- Embed line ----
    def _embed_line(e: dict | None) -> str:
        if not e:
            return "pending"
        n = e.get("n_clusters") or 0
        dims = e.get("dimensions") or 0
        prov = e.get("provider") or "?"
        return f"{n} clusters → {dims}d via {prov}"

    # ---- K-selection line ----
    def _k_line(kk: dict | None) -> str:
        if not kk:
            return "pending"
        return (
            f"k_meta={kk.get('k_meta')} · k_volume={kk.get('k_volume')} → "
            f"k_target={kk.get('k_target')} → final_k={kk.get('final_k')} "
            f"(clamp {kk.get('clamp_min')}–{kk.get('clamp_max')})"
        )

    # ---- KMeans summary line ----
    def _kmeans_line(km: dict | None) -> str:
        if not km:
            return "pending"
        return f"best_k={km.get('best_k')} · sizes={km.get('cluster_sizes')}"

    # ---- Thin merge line ----
    def _thin_line(t: dict | None) -> str:
        if not t:
            return "pending"
        n_merges = len(t.get("merges") or [])
        thresh = t.get("threshold_files")
        return f"{n_merges:,} merges (threshold <{thresh} files)"

    # ---- Split line ----
    def _split_line(sp: dict | None) -> str:
        if not sp:
            return "pending"
        n_splits = len(sp.get("splits") or [])
        frac = sp.get("file_cap_fraction")
        frac_str = f"{frac:.0%}" if isinstance(frac, (int, float)) else "—"
        return f"{n_splits:,} splits (file_cap = {frac_str} of corpus)"

    # ---- KMeans sweep badges ----
    sweep_badges = []
    if kmeans:
        for s in (kmeans.get("sweep") or []):
            k_val = s.get("k")
            ch_val = s.get("ch")
            sil_val = s.get("silhouette")
            parts = [f"k={k_val}"]
            if ch_val is not None:
                parts.append(f"CH={ch_val:.1f}")
            if sil_val is not None:
                parts.append(f"sil={sil_val:.3f}")
            sweep_badges.append(Span(
                ": ".join([parts[0], " ".join(parts[1:])]) if len(parts) > 1 else parts[0],
                cls="text-xs font-mono px-2 py-1 bg-base-200 rounded mr-2 inline-block",
            ))

    return _SubstepCard(
        "2.9 REDUCE (deterministic clustering)",
        "Clio pattern · zero LLM calls for clustering itself",
        Div(
            Div(
                Span("Embed", cls="text-xs font-semibold opacity-70 block"),
                Span(_embed_line(embed), cls="text-xs"),
                cls="mb-2",
            ),
            Div(
                Span("PCA + UMAP", cls="text-xs font-semibold opacity-70 block"),
                Span(_umap_line(umap), cls="text-xs"),
                cls="mb-2",
            ),
            Div(
                Span("K selection", cls="text-xs font-semibold opacity-70 block"),
                Span(_k_line(k), cls="text-xs font-mono"),
                cls="mb-2",
            ),
            Div(
                Span("KMeansConstrained sweep",
                     cls="text-xs font-semibold opacity-70 block"),
                Span(_kmeans_line(kmeans), cls="text-xs font-mono"),
                Div(*sweep_badges, cls="mt-1 flex flex-wrap gap-1")
                if sweep_badges else "",
                cls="mb-2",
            ),
            Div(
                Span("Thin-chapter merge",
                     cls="text-xs font-semibold opacity-70 block"),
                Span(_thin_line(thin), cls="text-xs"),
                cls="mb-2",
            ),
            Div(
                Span("Oversize-chapter split",
                     cls="text-xs font-semibold opacity-70 block"),
                Span(_split_line(split), cls="text-xs"),
            ),
        ),
    )


def _ChapterCoherenceCard(d: dict | None) -> Div:
    """
    The Ch02 mis-routing detector — chapter title coherence vs assigned files.
    """
    if not d:
        return _SubstepCard(
            "2.9g Chapter coherence (Ch02 detector)",
            "cos(title_emb, file_emb) per chapter",
            None, "pending",
        )
    chapters = d.get("chapters") or []
    threshold_red = d.get("threshold_red", 0.35)
    threshold_yellow = d.get("threshold_yellow", 0.50)

    rows = []
    for ch in chapters:
        score = ch.get("coherence_score", 0.0)
        if score < threshold_red:
            score_cls = "text-error font-semibold"
            row_cls = "bg-error/5"
        elif score < threshold_yellow:
            score_cls = "text-warning font-semibold"
            row_cls = "bg-warning/5"
        else:
            score_cls = "text-success"
            row_cls = ""
        rows.append(Tr(
            Td(Span(f"#{ch.get('number')}", cls="text-xs font-mono")),
            Td(Span(ch.get("title", "?"), cls="text-xs")),
            Td(Span(f"{ch.get('n_files', 0):,}", cls="text-xs")),
            Td(Span(f"{score:.3f}", cls=f"text-xs font-mono {score_cls}")),
            cls=row_cls,
        ))

    return _SubstepCard(
        "2.9g Chapter coherence (Ch02 detector)",
        f"red <{threshold_red:.2f} · yellow <{threshold_yellow:.2f} · {d.get('embedding_provider') or 'pending'}",
        Div(
            Table(
                Thead(Tr(
                    Th("#"), Th("Title"), Th("Files"), Th("Coherence"),
                )),
                Tbody(*rows),
                cls="table table-xs",
            ),
            cls="overflow-x-auto",
        ) if rows else Span("no chapters yet", cls="text-xs opacity-50"),
    )


def _ValidationCard(v: dict | None, c: dict | None) -> Div:
    if not v and not c:
        return _SubstepCard("2.11 Validation + 2.12 Coverage",
                            "structural + slug coverage checks",
                            None, "pending")
    body_children = []
    if v:
        body_children.append(Div(
            Span("Validation",
                 cls="text-xs font-semibold opacity-70 block mb-1"),
            Div(
                _Stat("Valid",
                      "✓" if v.get("is_valid") else "✗",
                      "no warnings" if v.get("is_valid") else f"{len(v.get('warnings') or [])} warnings"),
                _Stat("Orphans",
                      f"{(v.get('orphan_count') or 0):,}",
                      f"{(v.get('drop_rate') or 0):.1%} drop rate"),
                _Stat("Hallucinated",
                      f"{(v.get('hallucinated_count') or 0):,}",
                      None),
                _Stat("Duplicates",
                      "yes" if v.get("has_duplicates") else "no",
                      None),
                cls="grid grid-cols-2 md:grid-cols-4 gap-3",
            ),
            cls="mb-3",
        ))
    if c:
        body_children.append(Div(
            Span("Coverage repair",
                 cls="text-xs font-semibold opacity-70 block mb-1"),
            Div(
                _Stat("Orphans → unused",
                      f"{c.get('orphans_added', 0):,}",
                      "auto-parked"),
                _Stat("Hallucinated dropped",
                      f"{c.get('hallucinated_dropped', 0):,}",
                      None),
                cls="grid grid-cols-2 gap-3",
            ),
            Span(
                ", ".join(c.get("orphans_examples") or [])[:200],
                cls="text-xs opacity-60 mt-1 block break-all",
            ) if c.get("orphans_examples") else "",
        ))
    return _SubstepCard(
        "2.11 Validation + 2.12 Coverage repair",
        None,
        Div(*body_children),
    )


def _PerURLTable(urls: list[dict]) -> Div:
    if not urls:
        return Div(
            Div(
                Span("No URLs recorded yet. The first record appears once a "
                     "tier ingester fires its first fetch.",
                     cls="text-sm opacity-60"),
                cls="p-6 text-center",
            ),
            cls="mt-6 bg-base-100 border border-base-300 rounded-lg",
        )

    # Newest first — table scrolls; no pagination per user direction
    # (keep all URLs; 1,341 Docker URLs render fine in a single page).
    rows = []
    for u in reversed(urls):
        url = u.get("url") or ""
        status = u.get("status")
        http_code = u.get("http_code")
        fetch_ms = u.get("fetch_ms")
        bytes_v = u.get("bytes")
        extracted = u.get("extracted_chars")
        tier = u.get("tier") or ""
        err = u.get("error_msg")

        # Row color hint for the eye
        row_cls = ""
        if status in ("http_error", "fetch_error", "timeout"):
            row_cls = "bg-error/5"
        elif status in ("extract_empty", "downgrade"):
            row_cls = "bg-warning/5"

        rows.append(
            Tr(
                Td(_status_chip(status), cls="whitespace-nowrap"),
                Td(Span(tier, cls="text-xs font-mono opacity-70")),
                Td(
                    Code(
                        _truncate(url, 80),
                        title=url,
                        cls="text-xs font-mono break-all",
                    ),
                ),
                Td(
                    Span(str(http_code) if http_code is not None else "—",
                         cls="text-xs font-mono"),
                ),
                Td(
                    Span(_fmt_ms(fetch_ms),
                         cls="text-xs font-mono tabular-nums"),
                ),
                Td(
                    Span(_fmt_bytes(bytes_v),
                         cls="text-xs font-mono tabular-nums"),
                ),
                Td(
                    Span(f"{extracted:,}" if isinstance(extracted, int) else "—",
                         cls="text-xs font-mono tabular-nums"),
                ),
                Td(
                    Span(_truncate(err, 60) if err else "",
                         title=err or "",
                         cls="text-xs opacity-70 break-all"),
                ),
                cls=row_cls,
            )
        )

    return Div(
        Div(
            H2(
                Span("Per-URL records",
                     cls="text-base font-semibold"),
                Span(f"({len(urls):,})",
                     cls="ml-2 text-xs opacity-50 font-normal"),
                cls="px-4 py-3 border-b border-base-300",
            ),
            Div(
                Table(
                    Thead(
                        Tr(
                            Th("Status", cls="text-xs font-semibold opacity-70"),
                            Th("Tier", cls="text-xs font-semibold opacity-70"),
                            Th("URL", cls="text-xs font-semibold opacity-70"),
                            Th("HTTP", cls="text-xs font-semibold opacity-70"),
                            Th("Fetch ms", cls="text-xs font-semibold opacity-70 text-right"),
                            Th("Bytes", cls="text-xs font-semibold opacity-70 text-right"),
                            Th("Extracted", cls="text-xs font-semibold opacity-70 text-right"),
                            Th("Error", cls="text-xs font-semibold opacity-70"),
                        ),
                    ),
                    Tbody(*rows),
                    cls="table table-xs table-zebra",
                ),
                cls="overflow-x-auto",
            ),
            cls="bg-base-100 border border-base-300 rounded-lg",
        ),
        cls="mt-6",
    )
