"""SynthState — TypedDict shared across all synth graph nodes."""
from __future__ import annotations

from typing import Optional, TypedDict


class SynthState(TypedDict, total=False):
    framework_slug: str
    chapter_id:     str            # e.g. "ch-03-runtime"
    thread_id:      str            # also LangFuse session_id
    synth_mode:     str            # "quality" (default) | "fast"

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

    status:         Optional[str]  # "running" | "done" | "failed" | "cancelled"
    error:          Optional[str]
