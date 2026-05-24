"""SynthState — TypedDict that flows through every synth graph node.

Per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`, the synth graph runs
PER-CHAPTER (not per-framework). One synth run = one chapter end-to-end:
outline_sdp → digest_construct → sawc_write → checklist_eval →
mgsr_replan → render_audit_write. The router fans out N concurrent
runs (one per chapter in `planner/{slug}/plan-latest.json`) — each
gets its own thread_id + checkpoint stream.

Fields are deliberately Optional so the IMPLEMENTED-subset graph builder
can produce a runnable graph with only a prefix of nodes wired, and so
a partial-replay (e.g. resume after a pod restart at the `outline_sdp`
checkpoint) starts cleanly from whatever state survived.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class SynthState(TypedDict, total=False):
    # ── inputs (set at graph kick-off) ─────────────────────────────────
    framework_slug: str
    chapter_id:     str            # e.g. "ch-03-runtime"
    thread_id:      str            # also LangFuse session_id
    synth_mode:     str            # "quality" (default) | "fast"

    # ── outline_sdp output ─────────────────────────────────────────────
    # MinIO key of the latest pointer (`synth/{slug}/{chapter_id}/
    # outline-latest.json`). Versioned blob lives at
    # `synth/{slug}/{chapter_id}/outline/{manifest_hash}.json`.
    outline_path:   Optional[str]
    outline_stats:  Optional[dict] # counts + DAG shape + cache_hit + wall_ms

    # ── digest_construct output (future) ───────────────────────────────
    digest_path:    Optional[str]
    digest_stats:   Optional[dict]

    # ── sawc_write output (future) ─────────────────────────────────────
    sawc_path:      Optional[str]
    sawc_stats:     Optional[dict]

    # ── checklist_eval output (future) ─────────────────────────────────
    checklist_path:  Optional[str]
    checklist_stats: Optional[dict]

    # ── mgsr_replan output (future) ────────────────────────────────────
    mgsr_path:      Optional[str]
    mgsr_stats:     Optional[dict]

    # ── render_audit_write output (future) ─────────────────────────────
    chapter_path:   Optional[str]
    chapter_stats:  Optional[dict]

    # ── mgsr→sawc loop closure (2026-05-24, CoRefine-style halting) ────
    # Iteration counter incremented each time sawc_write fires. Used by
    # the conditional edge after mgsr_replan to enforce the 5-iter budget.
    refine_iter:           Optional[int]
    # Previous iteration's checklist pass_rate, used for plateau detection
    # (halt when |this_score - prev_score| < 0.03 AND iter >= 2).
    prev_checklist_score:  Optional[float]
    # OP-12 best-seen rescue: track the sawc artifact (manifest hash + score)
    # with the highest checklist_pass_rate so far. On budget/plateau halt,
    # we route the best-seen — not necessarily the latest — to render.
    best_seen_sawc_path:   Optional[str]
    best_seen_score:       Optional[float]

    # ── bookkeeping ────────────────────────────────────────────────────
    status:         Optional[str]  # "running" | "done" | "failed" | "cancelled"
    error:          Optional[str]
