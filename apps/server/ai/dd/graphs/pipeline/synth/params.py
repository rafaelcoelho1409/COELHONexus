"""Tunable scalars of the chapter graph (when the CoRefine loop stops) and of the study around it."""
from __future__ import annotations

import os

from ... import pipeline


# Canonical order of the nodes. corpus_normalize and the code vaults are ingestion-time work; the per-stage MinIO caches are inside each node.
NODE_ORDER = (
    "outline_sdp",
    "digest_construct",
    "sawc_write",
    "sawc_derive",
    "checklist_eval",
    "mgsr_replan",
    "render_audit_write",
)

# Synth v2 replaces the per-source LLM digest with a code-ledger preparation stage and
# reports code verification as its own checkpointed stage. Keep this tuple literal:
# the web contract checker compares it with SYNTH_V2_NODES.
V2_NODE_ORDER = (
    "outline_sdp",
    "ledger_prepare",
    "sawc_write",
    "sawc_derive",
    "verify",
    "checklist_eval",
    "mgsr_replan",
    "render_audit_write",
)


def version() -> str:
    """The active graph version. Unknown values fall back to the established v1 graph."""
    return "v2" if os.getenv("DD_SYNTH_VERSION", "v2").strip().lower() == "v2" else "v1"


def is_v2() -> bool:
    return version() == "v2"


def tutorial_mode() -> bool:
    """Enable tutorial behavior for Synth v2 without changing Planner's version selection."""
    return is_v2() or pipeline.params.TUTORIAL_MODE


def node_order() -> tuple[str, ...]:
    return V2_NODE_ORDER if is_v2() else NODE_ORDER

CHECKLIST_THRESHOLD = 0.80
MAX_REFINE_ITER = 5
MAX_V2_REFINE_ITER = 2  # initial write plus one targeted revision
PLATEAU_DELTA = 0.03

# chapters below 0.5 at iter-1 rarely recover above 0.80; best-seen rescue at iter-1 ≈ iter-2
NO_RECOVERY_FLOOR = 0.50

# Consecutive RETHINK iterations whose low score was infra-driven (judge call failures, not genuine content review) before halting early
# instead of burning the full MAX_REFINE_ITER budget against an outage that isn't clearing. Confirmed live: 3+ in a row produced zero
# improvement over ~17 extra minutes of compute. Unbenchmarked threshold — tune against real study runs, not a measured optimum.
SUSTAINED_INFRA_OUTAGE_LIMIT = 2

# Single-chapter Celery task limits — the task decorators import these (single source of truth; the wall-clock gate reads the soft limit too).
# The soft→hard gap is Celery's own documented minimum (300s) for a task to reliably clean up after the soft limit fires.
SINGLE_CHAPTER_SOFT_TIME_LIMIT_S = 3600.0
SINGLE_CHAPTER_HARD_TIME_LIMIT_S = 3900.0

# Wall-clock RETHINK gate (2026-09-11 incident): a run against a thinned pool burned the full soft_time_limit across ~1.5 RETHINK
# iterations and was hard-killed by Celery mid-write with ZERO output. Before looping back into another sawc_write iteration, halt to
# best-seen-rescue instead if the remaining budget can't plausibly fit one — estimated from the last sawc_write's own measured wall_ms
# times this safety margin.
WALL_CLOCK_SAFETY_MARGIN = 1.15
# No prior measurement to estimate from (shouldn't happen past iter 1): assume the worst cost actually observed live (~28 min).
FALLBACK_SAWC_WRITE_COST_S = 1700.0


def study_sem() -> int:
    """Chapters of a study worked on at once. API-bound on K8s; DD_STUDY_SEM=1 is strict order, 2 doubles throughput without contention (book_harmonize post-serializes).

    A function, not a module-level constant, so this file has zero import-time env dependency (docs/CODE-CONVENTIONS.md §8 Exception 1)."""
    return int(os.environ["DD_STUDY_SEM"])
