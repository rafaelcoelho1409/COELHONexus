"""SynthState — TypedDict shared across all synth graph nodes."""
from __future__ import annotations

from typing import Optional, TypedDict


class SynthState(TypedDict, total=False):
    framework_slug: str
    chapter_id:     str            # e.g. "ch-03-runtime"
    thread_id:      str            # also LangFuse session_id
    synth_mode:     str            # "quality" (default) | "fast"

    # Wall-clock start of the CURRENT Celery task execution (time.time()),
    # set fresh in run_single_chapter_async's initial_state and reset on
    # resume_synth_async (each is its own task invocation with its own
    # soft_time_limit clock). Absent → the wall-clock RETHINK gate in
    # graph.py no-ops (safe default for entry points that don't set it,
    # e.g. run_study_async's per-book orchestration).
    run_started_at: Optional[float]

    outline_path:   Optional[str]
    outline_stats:  Optional[dict] # counts + DAG shape + cache_hit + wall_ms

    digest_path:    Optional[str]
    digest_stats:   Optional[dict]

    sawc_path:      Optional[str]
    sawc_stats:     Optional[dict]

    # Side effect: mutates sawc-latest.json in place to embed derived_code on affected subtopics.
    derive_stats:   Optional[dict]

    checklist_path:  Optional[str]
    checklist_stats: Optional[dict]

    mgsr_path:      Optional[str]
    mgsr_stats:     Optional[dict]

    chapter_path:   Optional[str]
    chapter_stats:  Optional[dict]

    refine_iter:           Optional[int]
    # Plateau detection: halt when |this_score - prev_score| < PLATEAU_DELTA AND iter >= 2.
    prev_checklist_score:  Optional[float]
    # OP-12 best-seen rescue: on budget/plateau halt, route highest-score sawc to render.
    best_seen_sawc_path:   Optional[str]
    best_seen_score:       Optional[float]
    # Tie-breaker for best_seen_score (issue #12, 2026-09-06): a tie on
    # pass_rate can be an artifact of a judge call failing that round
    # rather than genuinely equal quality — n_pregate_passed (deterministic
    # checks, judge-independent) breaks ties toward real structural
    # completeness. Compared as (best_seen_score, best_seen_pregate).
    best_seen_pregate:     Optional[int]
    # Sustained-outage detection: consecutive RETHINK iterations whose low
    # score was infra-driven (judge/CoCoA/atomic-claim call failures), not
    # genuine content review. Resets to 0 the moment an iteration is NOT
    # infra-degraded. Distinct from refine_iter (total budget spent).
    consecutive_infra_degraded: Optional[int]

    status:         Optional[str]  # "running" | "done" | "failed" | "cancelled"
    error:          Optional[str]
