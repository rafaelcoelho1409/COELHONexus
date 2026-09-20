"""Triage — async orchestrator tool. Imperative Shell.

The deterministic Phase-2 node from the architecture doc. Reads the 4
discovery outputs from the scan's virtual fs, runs the domain pipeline
(see ./domain.py), and writes the ranked list back to fs.

2026-06-15 UPGRADES (Fixes #3+#4):
- Off-topic rerank gate via NIM rerank-1b cross-encoder — REMOVED
  2026-09-17. It called `rerank_via_router_async`, a hardcoded stub
  that always returned `[]`; no rerank infra was ever actually built
  for this codebase's rotator, so the gate was a permanent no-op
  pass-through in every real run — removing it changes nothing about
  runtime behavior, just deletes dead code. The failure mode it was
  meant to catch (an off-topic-but-popular paper surviving because HF
  daily papers have no categories → vertical_fit was 0) is still
  possible; if it shows up as a real, observed problem, the cheaper
  fix is an LLM-judge relevance call through the existing chat
  rotator connection, not a dedicated rerank model.
- Source-diversity quota. When ≥2 sources contributed ≥`MIN_PER_SOURCE_FLOOR`
  candidates each, force min-1-per-source in the top_n so a single
  source can't monopolize the digest (HF daily had all 4 ranks in
  scan fd48309a).
"""
from __future__ import annotations

import json
import logging
from datetime import date

from langchain_core.tools import tool

from . import domain
from .. import state as tools_state
from ... import keys
from .... import domain as rr_domain
from .... import params as rr_params
from .... import runtime


logger = logging.getLogger(__name__)


@tool
async def triage_candidates(
    scan_id: str,
    topic: str,
    profile_verticals: list[str] | None = None,
    top_n: int = 12,
) -> str:
    """Rank discovery candidates by topical relevance + signal_score; write
    top-N to fs/triage.

    Call this AFTER all 5 discovery subagents have returned (their results
    are stashed in fs under `discovery/<source>.json` by their stash tool
    calls).

    Args:
        scan_id: Identifier for this radar scan (provided in your initial
            user message — pass it through).
        topic: The user's topic string from the initial message (e.g.
            'deep agents'). Currently unused by the ranking pipeline
            itself (the off-topic rerank gate that consumed it was
            removed — see module docstring); kept in the signature for
            logging and any future relevance gate.
        profile_verticals: Profile's vertical categories (e.g. ['cs.LG',
            'cs.AI', 'q-fin.PR']). Pass an empty list if the user didn't
            specify any.
        top_n: How many papers to keep for deep_read. Defaults to 12;
            range 8-20 is reasonable.

    Returns:
        A short summary including the count of candidates examined, the
        count after dedup + off-topic filter, and the path written.
    """
    # Idempotency guard.
    # If `fs/triage/top_n.json` already exists for this scan, REFUSE to
    # overwrite. Return the existing top_arxiv_ids so the orchestrator's
    # Phase 3 dispatch logic still works — but DON'T re-rank, DON'T change
    # `top_n`, DON'T re-prefill from cache.
    # Why: scan `20f4e4af` showed the orchestrator re-calling triage AFTER
    # synthesis completed (with `top_n=12` and `topic='general'` — both
    # values the LLM invented to try to "broaden the search" after a
    # ScanComplete validation failure). The result was top_n.json was
    # overwritten, 4 of 12 new deep_reads ran, synthesis was NOT re-run,
    # and the final digest had 2/12 papers themed.
    # The guard breaks the loop at the source: the second call returns a
    # message saying "already done, use these arxiv_ids" → the orchestrator
    # can't change the scan's identity mid-flight. Single-source-of-truth
    # for top_n.json per scan.
    existing_top_n = tools_state.fs_read(scan_id, keys.FS_FILE_TRIAGE_TOPN)
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
    candidates: list = []
    per_source_counts: dict[str, int] = {}
    for source, normalizer in domain.NORMALIZER_BY_SOURCE.items():
        path = keys.fs_discovery_path(source)
        raw = tools_state.fs_read(scan_id, path)
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
        tools_state.fs_write(scan_id, keys.FS_FILE_TRIAGE_TOPN, [])
        try: runtime.service.mirror_write_sync(scan_id, keys.FS_FILE_TRIAGE_TOPN, [])
        except Exception: pass
        return msg

    # Cross-source dedup — the architectural payoff (architecture doc §4).
    deduped = rr_domain.dedup_by_arxiv_id(candidates)
    # drop papers without arxiv_id BEFORE rerank +
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
        tools_state.fs_write(scan_id, keys.FS_FILE_TRIAGE_TOPN, [])
        try: runtime.service.mirror_write_sync(scan_id, keys.FS_FILE_TRIAGE_TOPN, [])
        except Exception: pass
        return msg

    # 2026-09-17: the off-topic rerank gate (`_topical_rerank_filter`,
    # NIM cross-encoder) was removed — `rerank_via_router_async` it
    # called was a hardcoded stub always returning `[]` (no rerank
    # infra was ever actually built for this codebase's rotator), so
    # this gate was ALREADY a permanent no-op pass-through in every
    # real run. `topic` is kept as a parameter (still used for logging
    # + kept for callers) but no longer filters anything; signal_score
    # alone drives ranking, same as it already effectively did.
    relevant = deduped

    # Score each — pure function. embedding=None means relevance term = 0;
    # vertical_fit + recency + buzz + velocity + influential_ratio drive
    # the ranking until the embedding pipeline lands (step 3+ embed_via_router).
    now = date.today()
    verticals = tuple(profile_verticals or ())
    scored = [
        (p, rr_domain.signal_score(
            p,
            now               = now,
            profile_embedding = None,
            profile_verticals = verticals,
            weights           = rr_params.WEIGHTS,
            domain_params     = rr_params.DOMAIN_PARAMS,
        ))
        for p in relevant
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Source-diversity quota — when ≥2 sources have real content, force
    # min-1-per-source in the top_n so a single source can't monopolize.
    top = domain.diversify_by_source(scored, max(1, int(top_n)), per_source_counts)

    # topical_logit is always None now (no rerank gate — see above);
    # kept as an explicit field since task.py's digest assembly reads it.
    payload = [
        domain.paper_as_dict(
            p, score=s,
            topical_logit=None,
        )
        for p, s in top
    ]
    tools_state.fs_write(scan_id, keys.FS_FILE_TRIAGE_TOPN, payload)
    try: runtime.service.mirror_write_sync(scan_id, keys.FS_FILE_TRIAGE_TOPN, payload)
    except Exception: pass
    # Phase contextvar for LLM-counter attribution (Path A).
    # The next LLM calls (orchestrator dispatching deep_read fan-out)
    # attribute to "triage" until the first write_extraction lands.
    try:
        runtime.llm_counter.service.set_phase("triage")
    except Exception: pass

    # cross-scan extraction cache prefill.
    # DISABLED — observed behavior across scans 96173afd,
    # 157644c6, c6fe7b76 (cold / 1-repeat / 2-repeat) showed the cache
    # prefill consistently DEGRADED end-to-end wall time + correctness:
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
    # Root cause is the orchestrator's strict-phase emission: the LLM
    # can't prove "I dispatched deep_read for these papers" when the cache
    # prefilled them, so ScanComplete validation fails ("deep_read not
    # completed") → framework re-prompts → LLM's recovery strategy is to
    # re-dispatch (or worse, re-run triage with a higher top_n to "get
    # more findings"). Net result: cache makes every repeat scan slower
    # and more chaotic than a cold scan.
    # Wave 1+2 (bandit + 9-arm pool + Semaphore + per-provider caps)
    # already deliver the speedup target (5min vs 10-20min baseline);
    # the cache layer was a speculative add-on that didn't pay off in
    # practice for a RECENT-papers radar where natural cross-scan
    # overlap is low and ScanComplete validation is strict.
    # PRESERVED for future re-enable:
    #   - The cache module itself (`domains.rr.runtime.service`'s
    #     extraction-cache section)
    #   - `write_extraction` still calls `runtime.service.set_extraction_sync`
    #     so the cache builds up as scans run — when we have a
    #     non-disruptive way to surface cached extractions to the
    #     orchestrator (e.g. via a separate context bundle to synthesis,
    #     NOT a fs prefill), we can re-enable.
    #   - The cache-aware orchestrator prompt branches (Phase 3
    #     "to_dispatch = top_arxiv_ids - cached_arxiv_ids" + the
    #     CRITICAL marker for ScanComplete's deep_read.completed=True
    #     semantics). With cached_arxiv_ids=[] always, the conditional
    #     branches are dead code — but they're defensive guidance the
    #     orchestrator can use for any future "phantom extractions"
    #     scenario, so we leave them in.
    # TO RE-ENABLE: uncomment the prefill call below. Recommend pairing
    # with the triage-idempotency guard (refuse a 2nd triage call per
    # scan) to prevent the orchestrator's loop fallback.
    cached_arxiv_ids: list[str] = []
    #     cached_arxiv_ids = await runtime.service.prefill_extractions_from_cache(scan_id, payload)
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
        f"deduped={len(deduped)} "
        f"top_n={len(top)} per_source={per_source_counts} "
        f"{score_range}"
        f"top_arxiv_ids={top_arxiv_ids}{cache_note}"
    )
    logger.info(msg)
    return msg
