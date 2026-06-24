"""Triage — async orchestrator tool with off-topic rerank gate.

The deterministic Phase-2 node from the architecture doc. Reads the 4
discovery outputs from the scan's virtual fs, runs the domain pipeline:

  normalize → dedup_by_arxiv_id → topical rerank → diversify by source
  → signal_score → top-N

Writes the ranked list back to fs.

2026-06-15 UPGRADES (Fixes #3+#4):
- Off-topic rerank gate via NIM rerank-1b cross-encoder. Drops papers
  whose topical relevance to the user's query string scores below the
  bottom quantile. Catches the OmniDirector-at-rank-1 failure mode
  (camera cloning paper surviving a "deep agents" query because HF
  daily papers have no categories → vertical_fit was 0).
- Source-diversity quota. When ≥2 sources contributed ≥`MIN_PER_SOURCE_FLOOR`
  candidates each, force min-1-per-source in the top_n so a single
  source can't monopolize the digest (HF daily had all 4 ranks in
  scan fd48309a).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from langchain_core.tools import tool

from ..keys import (
    FS_FILE_TRIAGE_TOPN,
    fs_discovery_path,
)
from ...domain import (
    dedup_by_arxiv_id,
    diff_vs_seen,
    normalize_arxiv,
    normalize_hf,
    normalize_hn,
    normalize_s2,
    signal_score,
)
from ...entities import NormalizedPaper
from ...keys import (
    SOURCE_ARXIV,
    SOURCE_HF,
    SOURCE_HN,
    SOURCE_S2,
)
from ...params import WEIGHTS, DOMAIN_PARAMS
from .state import fs_list, fs_read, fs_write
from ...runtime.fs_mirror import mirror_write_sync


logger = logging.getLogger(__name__)


# Source → normalizer mapping. Looked up at runtime so we can stay tolerant
# to a missing discovery output (e.g. the hn subagent crashed mid-scan).
_NORMALIZER_BY_SOURCE = {
    SOURCE_ARXIV: normalize_arxiv,
    SOURCE_S2:    normalize_s2,
    SOURCE_HF:    normalize_hf,
    SOURCE_HN:    normalize_hn,
}


# Off-topic rerank — keep candidates whose rerank logit is >= the
# OFF_TOPIC_KEEP_QUANTILE of the candidate set. 0.50 = keep top half.
# Rerank logits are unbounded reals (~[-12, +12] on NIM nemotron-rerank-1b);
# quantile gate is more robust than an absolute threshold across topics.
_OFF_TOPIC_KEEP_QUANTILE: float = 0.50

# Source-diversity quota — only kicks in when ≥2 sources EACH contributed
# at least this many candidates. Below the floor we don't force diversity
# (a 5-arxiv + 0-hn pool shouldn't be artificially split).
_MIN_PER_SOURCE_FLOOR: int = 3


def _topic_summary_text(p: NormalizedPaper) -> str:
    """Concatenate title + abstract into a single dense string for rerank.
    Title carries the most discriminating signal; abstract gives the
    model enough context for borderline cases. Length-capped to ~4 KB so
    the rerank API call payload stays well under NIM's limit even with
    50 candidates in flight."""
    title = (p.title or "").strip()
    abstract = (p.abstract or "").strip()
    if not title and not abstract:
        return "(no text)"
    combined = f"{title}\n\n{abstract}"
    return combined[:4096]


async def _topical_rerank_filter(
    candidates: list[NormalizedPaper], topic: str,
) -> tuple[list[NormalizedPaper], dict[str, float]]:
    """Score every candidate by topical relevance to `topic`; keep the
    top-quantile fraction. Returns (survivors, per_arxiv_logit_dict).

    Fail-open: if the rerank API errors (rate-limit, network), we keep
    all candidates and let signal_score handle ordering. Empty-topic →
    no-op pass-through (the orchestrator caller should always provide
    a topic, but be defensive)."""
    if not topic.strip() or len(candidates) <= 1:
        return list(candidates), {}
    try:
        from domains.llm.rotator.chain import rerank_via_router_async
    except Exception as e:
        logger.warning(f"[triage] rerank import failed: {e}; passing through")
        return list(candidates), {}
    documents = [_topic_summary_text(p) for p in candidates]
    try:
        pairs = await rerank_via_router_async(topic, documents)
    except Exception as e:
        logger.warning(
            f"[triage] off-topic rerank failed: {e}; passing through all {len(candidates)} candidates"
        )
        return list(candidates), {}
    if not pairs:
        return list(candidates), {}
    # NIM returns (orig_index, logit) sorted descending. Pick the keep set
    # by the OFF_TOPIC_KEEP_QUANTILE.
    keep_count = max(1, int(round(len(pairs) * _OFF_TOPIC_KEEP_QUANTILE)))
    survivor_indices = {idx for idx, _logit in pairs[:keep_count]}
    survivors = [candidates[i] for i in range(len(candidates)) if i in survivor_indices]
    # Per-arxiv logit dict for telemetry/debugging.
    logit_by_id: dict[str, float] = {}
    for idx, logit in pairs:
        aid = candidates[idx].arxiv_id
        if aid:
            logit_by_id[aid] = float(logit)
    logger.info(
        f"[triage] off-topic rerank: kept {len(survivors)}/{len(candidates)} "
        f"(quantile={_OFF_TOPIC_KEEP_QUANTILE}, topic={topic!r}) "
        f"logit_by_id_total={len(logit_by_id)} "
        f"survivor_arxiv_ids={[p.arxiv_id for p in survivors]}"
    )
    return survivors, logit_by_id


def _diversify_by_source(
    scored: list[tuple[NormalizedPaper, float]],
    top_n: int,
    per_source_counts: dict[str, int],
) -> list[tuple[NormalizedPaper, float]]:
    """Force min-1-per-source in the top-N when multiple sources have
    real content. Picks the highest-scored candidate from each qualifying
    source first, then fills remaining slots in pure score order. Falls
    back to score-only when only one source qualifies."""
    qualifying_sources = {
        src for src, n in per_source_counts.items() if n >= _MIN_PER_SOURCE_FLOOR
    }
    if len(qualifying_sources) < 2:
        return scored[:top_n]
    # Score-order is preserved within each source's candidate list.
    by_source: dict[str, list[tuple[NormalizedPaper, float]]] = {
        s: [] for s in qualifying_sources
    }
    leftovers: list[tuple[NormalizedPaper, float]] = []
    for p, s in scored:
        # Pick the source the candidate belongs to. dedup-merged papers
        # carry the union; we use the first qualifying source for the quota.
        chosen_src: str | None = None
        for src in p.sources:
            if src in qualifying_sources:
                chosen_src = src
                break
        if chosen_src is not None:
            by_source[chosen_src].append((p, s))
        else:
            leftovers.append((p, s))
    # Step 1: take the BEST from each qualifying source (up to top_n).
    out: list[tuple[NormalizedPaper, float]] = []
    seen_ids: set[str] = set()
    for src in sorted(by_source.keys()):
        bucket = by_source[src]
        if not bucket:
            continue
        p, s = bucket[0]
        if p.arxiv_id and p.arxiv_id in seen_ids:
            continue
        out.append((p, s))
        if p.arxiv_id:
            seen_ids.add(p.arxiv_id)
        if len(out) >= top_n:
            return out
    # Step 2: fill remaining slots by pure score order from the merged pool.
    remaining_pool = [
        (p, s) for p, s in scored
        if not p.arxiv_id or p.arxiv_id not in seen_ids
    ]
    for p, s in remaining_pool:
        if len(out) >= top_n:
            break
        if p.arxiv_id and p.arxiv_id in seen_ids:
            continue
        out.append((p, s))
        if p.arxiv_id:
            seen_ids.add(p.arxiv_id)
    logger.info(
        f"[triage] diversity quota applied: {len(qualifying_sources)} qualifying sources "
        f"(min_floor={_MIN_PER_SOURCE_FLOOR}); top_n={len(out)} composed"
    )
    return out


@tool
async def triage_candidates(
    scan_id: str,
    topic: str,
    profile_verticals: list[str] | None = None,
    top_n: int = 12,
) -> str:
    """Rank discovery candidates by topical relevance + signal_score; write
    top-N to fs/triage.

    Call this AFTER all 4 discovery subagents have returned (their results
    are stashed in fs under `discovery/<source>.json` by their stash tool
    calls).

    Args:
        scan_id: Identifier for this radar scan (provided in your initial
            user message — pass it through).
        topic: The user's topic string from the initial message (e.g.
            'deep agents'). Used to off-topic-filter candidates via NIM
            cross-encoder rerank BEFORE signal scoring — keeps the digest
            relevant when HF daily papers contribute off-topic content.
        profile_verticals: Profile's vertical categories (e.g. ['cs.LG',
            'cs.AI', 'q-fin.PR']). Pass an empty list if the user didn't
            specify any.
        top_n: How many papers to keep for deep_read. Defaults to 12;
            range 8-20 is reasonable.

    Returns:
        A short summary including the count of candidates examined, the
        count after dedup + off-topic filter, and the path written.
    """
    # ────────────────────────────────────────────────────────────────────────
    # Idempotency guard (Fix #3 — 2026-06-16).
    #
    # If `fs/triage/top_n.json` already exists for this scan, REFUSE to
    # overwrite. Return the existing top_arxiv_ids so the orchestrator's
    # Phase 3 dispatch logic still works — but DON'T re-rank, DON'T change
    # `top_n`, DON'T re-prefill from cache.
    #
    # Why: scan `20f4e4af` showed the orchestrator re-calling triage AFTER
    # synthesis completed (with `top_n=12` and `topic='general'` — both
    # values the LLM invented to try to "broaden the search" after a
    # ScanComplete validation failure). The result was top_n.json was
    # overwritten, 4 of 12 new deep_reads ran, synthesis was NOT re-run,
    # and the final digest had 2/12 papers themed.
    #
    # The guard breaks the loop at the source: the second call returns a
    # message saying "already done, use these arxiv_ids" → the orchestrator
    # can't change the scan's identity mid-flight. Single-source-of-truth
    # for top_n.json per scan.
    # ────────────────────────────────────────────────────────────────────────
    existing_top_n = fs_read(scan_id, FS_FILE_TRIAGE_TOPN)
    if isinstance(existing_top_n, list) and existing_top_n:
        existing_ids = [
            p.get("arxiv_id") for p in existing_top_n
            if isinstance(p, dict) and p.get("arxiv_id")
        ]
        msg = (
            f"[triage] IDEMPOTENT — triage already ran for this scan_id. "
            f"top_arxiv_ids={existing_ids} top_n={len(existing_ids)} "
            f"(call args ignored: topic={topic!r}, top_n={top_n}, "
            f"profile_verticals={profile_verticals}). Proceed to Phase 3 "
            f"with these arxiv_ids — do NOT call triage_candidates again."
        )
        logger.warning(
            f"[triage] idempotent return scan_id={scan_id} "
            f"existing_top_n={len(existing_ids)} "
            f"refused_args=(topic={topic!r}, top_n={top_n})"
        )
        return msg

    # Read each source's stashed discovery output. Missing → empty list
    # (one failed source shouldn't block triage).
    candidates: list[NormalizedPaper] = []
    per_source_counts: dict[str, int] = {}
    for source, normalizer in _NORMALIZER_BY_SOURCE.items():
        path = fs_discovery_path(source)
        raw = fs_read(scan_id, path)
        if raw is None:
            per_source_counts[source] = 0
            continue
        # Tolerate string JSON (legacy path) or pre-parsed list
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[triage] {path} contained invalid JSON; skipping")
                per_source_counts[source] = 0
                continue
        if not isinstance(raw, list):
            logger.warning(f"[triage] {path} was {type(raw).__name__}, expected list")
            per_source_counts[source] = 0
            continue
        normalized = [normalizer(d) for d in raw if isinstance(d, dict)]
        candidates.extend(normalized)
        per_source_counts[source] = len(normalized)

    if not candidates:
        msg = f"[triage] no candidates from any source ({per_source_counts})"
        logger.warning(msg)
        fs_write(scan_id, FS_FILE_TRIAGE_TOPN, [])
        try: mirror_write_sync(scan_id, FS_FILE_TRIAGE_TOPN, [])
        except Exception: pass
        return msg

    # Cross-source dedup — the architectural payoff (architecture doc §4).
    deduped = dedup_by_arxiv_id(candidates)
    # Fix #4 (2026-06-16): drop papers without arxiv_id BEFORE rerank +
    # quota composition. Deep_read can only extract papers with an
    # arxiv_id (its tool reads from triage's top_n.json keyed by arxiv_id);
    # including arxiv-less papers (HN posts that didn't link to arxiv,
    # S2 entries without external IDs) in top_n inflates the denominator
    # → causes `partial_extractions_X_of_Y` degradation. Observed in scan
    # d196a862 (N=12 → only 9 fetchable arxiv_ids, 3 phantom slots).
    n_before_arxiv_filter = len(deduped)
    deduped = [p for p in deduped if p.arxiv_id]
    n_dropped_no_arxiv = n_before_arxiv_filter - len(deduped)
    if n_dropped_no_arxiv:
        logger.info(
            f"[triage] dropped {n_dropped_no_arxiv} arxiv-less papers "
            f"(HN posts without arxiv link, S2 without external_id); "
            f"{len(deduped)} arxiv-linked candidates remain"
        )

    if not deduped:
        msg = (
            f"[triage] no arxiv-linked candidates after filter "
            f"(dropped={n_dropped_no_arxiv} arxiv-less, total={n_before_arxiv_filter}); "
            f"per_source={per_source_counts}"
        )
        logger.warning(msg)
        fs_write(scan_id, FS_FILE_TRIAGE_TOPN, [])
        try: mirror_write_sync(scan_id, FS_FILE_TRIAGE_TOPN, [])
        except Exception: pass
        return msg

    n_before_rerank = len(deduped)

    # Off-topic rerank gate — drop the half whose topical relevance to
    # the user's `topic` string falls below median. Fail-open on API error.
    relevant, rerank_logits = await _topical_rerank_filter(deduped, topic)

    # Score each — pure function. embedding=None means relevance term = 0;
    # vertical_fit + recency + buzz + velocity + influential_ratio drive
    # the ranking until the embedding pipeline lands (step 3+ embed_via_router).
    now = date.today()
    verticals = tuple(profile_verticals or ())
    scored = [
        (p, signal_score(
            p,
            now               = now,
            profile_embedding = None,
            profile_verticals = verticals,
            weights           = WEIGHTS,
            domain_params     = DOMAIN_PARAMS,
        ))
        for p in relevant
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Source-diversity quota — when ≥2 sources have real content, force
    # min-1-per-source in the top_n so a single source can't monopolize.
    top = _diversify_by_source(scored, max(1, int(top_n)), per_source_counts)

    # Serialize top-N as a list of dicts for downstream subagents to read.
    # Attach rerank logit so downstream extraction can prioritize.
    payload = [
        _paper_as_dict(
            p, score=s,
            topical_logit=rerank_logits.get(p.arxiv_id or "", None),
        )
        for p, s in top
    ]
    fs_write(scan_id, FS_FILE_TRIAGE_TOPN, payload)
    try: mirror_write_sync(scan_id, FS_FILE_TRIAGE_TOPN, payload)
    except Exception: pass
    # Phase contextvar for LLM-counter attribution (Path A 2026-06-16).
    # The next LLM calls (orchestrator dispatching deep_read fan-out)
    # attribute to "triage" until the first write_extraction lands.
    try:
        from ...runtime.llm_counter import set_phase as _set_llm_phase
        _set_llm_phase("triage")
    except Exception: pass

    # Wave 1.7 (2026-06-16): cross-scan extraction cache prefill.
    #
    # ────────────────────────────────────────────────────────────────────────
    # DISABLED 2026-06-16 EOD — observed behavior across scans 96173afd,
    # 157644c6, c6fe7b76 (cold / 1-repeat / 2-repeat) showed the cache
    # prefill consistently DEGRADED end-to-end wall time + correctness:
    #
    #   - Cold run (no cache):        5:05  · 8 findings · 8 extractions
    #   - Repeat 1  (8/8 cached):     7:30  · 8 findings · 16 extractions
    #                                          (orchestrator re-extracted
    #                                          all 8 anyway — same arxiv_ids,
    #                                          fresh confidence values)
    #   - Repeat 2  (3-6/8 cached):   9:47  · 12 findings (!) · 17 extractions
    #                                          (orchestrator re-ran TRIAGE
    #                                          with top_n=12, then re-ran
    #                                          synthesis — completionist
    #                                          loop in extremis)
    #
    # Root cause is the orchestrator's strict-phase emission: the LLM
    # can't prove "I dispatched deep_read for these papers" when the cache
    # prefilled them, so ScanComplete validation fails ("deep_read not
    # completed") → framework re-prompts → LLM's recovery strategy is to
    # re-dispatch (or worse, re-run triage with a higher top_n to "get
    # more findings"). Net result: cache makes every repeat scan slower
    # and more chaotic than a cold scan.
    #
    # Wave 1+2 (bandit + 9-arm pool + Semaphore + per-provider caps)
    # already deliver the speedup target (5min vs 10-20min baseline);
    # the cache layer was a speculative add-on that didn't pay off in
    # practice for a RECENT-papers radar where natural cross-scan
    # overlap is low and ScanComplete validation is strict.
    #
    # PRESERVED for future re-enable:
    #   - The cache module itself (`runtime/extraction_cache.py`)
    #   - `write_extraction` still calls `_cache_extraction(arxiv_id,
    #     payload)` so the cache builds up as scans run — when we have
    #     a non-disruptive way to surface cached extractions to the
    #     orchestrator (e.g. via a separate context bundle to synthesis,
    #     NOT a fs prefill), we can re-enable.
    #   - The cache-aware orchestrator prompt branches (Phase 3
    #     "to_dispatch = top_arxiv_ids - cached_arxiv_ids" + the
    #     CRITICAL marker for ScanComplete's deep_read.completed=True
    #     semantics). With cached_arxiv_ids=[] always, the conditional
    #     branches are dead code — but they're defensive guidance the
    #     orchestrator can use for any future "phantom extractions"
    #     scenario, so we leave them in.
    #
    # TO RE-ENABLE: uncomment the prefill call below. Recommend pairing
    # with the triage-idempotency guard (refuse a 2nd triage call per
    # scan) to prevent the orchestrator's loop fallback.
    # ────────────────────────────────────────────────────────────────────────
    cached_arxiv_ids: list[str] = []
    # try:
    #     from ...runtime.extraction_cache import prefill_extractions_from_cache
    #     cached_arxiv_ids = await prefill_extractions_from_cache(scan_id, payload)
    # except Exception as e:
    #     logger.warning(f"[triage] extraction-cache prefill failed: {e}")

    # Surface the top arxiv_ids in the return string so the orchestrator's
    # LLM knows which IDs to dispatch deep_read for in Phase 3 without
    # having to read fs separately. Subagents that need full paper data
    # still load it via read_top_n_papers.
    top_arxiv_ids = [p.arxiv_id for p, _ in top if p.arxiv_id]
    # `cached_arxiv_ids` is the subset of top_arxiv_ids that already have
    # extractions on disk (cache hits). Always empty while prefill is
    # disabled — the orchestrator just dispatches deep_read for every
    # top_arxiv_id, which is the predictable 5min-cold-scan baseline.
    cache_note = (
        f" cached_arxiv_ids={cached_arxiv_ids} cached_extractions={len(cached_arxiv_ids)}"
        if cached_arxiv_ids else ""
    )
    score_range = (
        f"top_score={top[0][1]:.4f} bottom_score={top[-1][1]:.4f} "
        if top else "top_score=N/A bottom_score=N/A "
    )
    msg = (
        f"[triage] in={sum(per_source_counts.values())} "
        f"deduped={n_before_rerank} after_rerank={len(relevant)} "
        f"top_n={len(top)} per_source={per_source_counts} "
        f"{score_range}"
        f"top_arxiv_ids={top_arxiv_ids}{cache_note}"
    )
    logger.info(msg)
    return msg


def _paper_as_dict(
    p: NormalizedPaper, *, score: float, topical_logit: float | None = None,
) -> dict[str, Any]:
    """Materialize a NormalizedPaper as a JSON-safe dict for fs storage."""
    return {
        "arxiv_id":              p.arxiv_id,
        "title":                 p.title,
        "abstract":              p.abstract,
        "published":             p.published.isoformat() if p.published else None,
        "authors":               list(p.authors),
        "categories":            list(p.categories),
        "citations":             p.citations,
        "influential_citations": p.influential_citations,
        "hn_points":             p.hn_points,
        "hn_num_comments":       p.hn_num_comments,
        "hf_upvotes":            p.hf_upvotes,
        "sources":               sorted(p.sources),
        "signal":                float(score),
        "topical_logit":         (None if topical_logit is None else float(topical_logit)),
    }
