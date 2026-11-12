"""SynthState: one chapter's run. One output field per node, all optional so a partial replay never crashes. Field names are read by name by the web."""
from __future__ import annotations

import typing


class SynthState(typing.TypedDict, total = False):
    framework_slug: str
    chapter_id:     str            # e.g. "ch-03-runtime"
    thread_id:      str            # also LangFuse session_id
    synth_mode:     str            # "quality" (default) | "fast"

    # Wall-clock start of the CURRENT Celery task execution (time.time()), set fresh by a run and reset on a resume (each is its
    # own task with its own soft_time_limit clock). Absent → the wall-clock RETHINK gate in `nodes.pipeline.synth.mgsr.node.route_after_mgsr` no-ops.
    run_started_at: typing.Optional[float]

    outline_path:   typing.Optional[str]
    outline_stats:  typing.Optional[dict]  # counts + DAG shape + cache_hit + wall_ms

    digest_path:    typing.Optional[str]
    digest_stats:   typing.Optional[dict]

    # Synth v2's deterministic ledger stage writes the digest-compatible artifact consumed by the existing section writer.
    ledger_path:    typing.Optional[str]
    ledger_stats:   typing.Optional[dict]

    sawc_path:      typing.Optional[str]
    sawc_stats:     typing.Optional[dict]

    # Side effect: mutates sawc-latest.json in place to embed derived_code on affected subtopics.
    derive_stats:   typing.Optional[dict]

    checklist_path:  typing.Optional[str]
    checklist_stats: typing.Optional[dict]

    mgsr_path:      typing.Optional[str]
    mgsr_stats:     typing.Optional[dict]

    chapter_path:   typing.Optional[str]
    chapter_stats:  typing.Optional[dict]

    verify_path:    typing.Optional[str]
    verify_stats:   typing.Optional[dict]

    refine_iter:           typing.Optional[int]
    # Plateau detection: halt when |this_score - prev_score| < PLATEAU_DELTA AND iter >= 2.
    prev_checklist_score:  typing.Optional[float]
    # Best-seen rescue: on budget/plateau halt, route the highest-score draft to render.
    best_seen_sawc_path:   typing.Optional[str]
    best_seen_score:       typing.Optional[float]
    # Tie-breaker for best_seen_score: a tie on pass_rate can be an artifact of a judge call failing that round rather than genuinely
    # equal quality — n_pregate_passed (deterministic checks, judge-independent) breaks ties toward real structural completeness.
    best_seen_pregate:     typing.Optional[int]
    # Sustained-outage detection: consecutive RETHINK iterations whose low score was infra-driven (judge call failures), not genuine
    # content review. Resets to 0 the moment an iteration is NOT infra-degraded. Distinct from refine_iter (total budget spent).
    consecutive_infra_degraded: typing.Optional[int]

    status:         typing.Optional[str]  # "running" | "done" | "needs_review" | "failed" | "cancelled" | "audit_failed"
    error:          typing.Optional[str]
