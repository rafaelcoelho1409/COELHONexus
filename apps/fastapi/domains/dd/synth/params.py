"""Tunable scalars shared across the synth package."""
from __future__ import annotations

import os


CANCEL_TTL_S = 3600
SNAPSHOT_TTL_S = 86400   # 24h — covers overnight reloads while a study run is in flight
SNAPSHOT_MAX_EVENTS = 200

REDIS_CONNECT_TIMEOUT_S = 3.0
REDIS_OP_TIMEOUT_S = 5.0


# CoRefine-style halting (2026-05-24) — mgsr_replan → sawc_write loop closure
# Per docs/KD-SYNTH-SOTA-2026-05-24.md §3 #4: replace the strictly-linear
# mgsr_replan → render_audit_write edge with a conditional edge:
#
#   HALT (success) — checklist pass_rate >= 0.80  → render_audit_write
#   HALT (budget)  — refine_iter >= 5             → render_audit_write
#   HALT (plateau) — iter >= 2 AND |score - prev| < 0.03 → render_audit_write
#   RETHINK        — otherwise                    → sawc_write (loop back)
#
# OP-12 best-seen rescue: handled inside sawc_write/render_audit_write —
# the state tracks best_seen_sawc_path so even after a budget/plateau halt
# we render the highest-scoring iteration.
CHECKLIST_THRESHOLD = 0.80
MAX_REFINE_ITER = 5
PLATEAU_DELTA = 0.03

# Bundle 7 (2026-05-25) — Iter-1 short-circuit (no-recovery threshold).
# Empirical: chapters scoring < 0.5 at iter=1 almost never recover above
# 0.80 by iter=2 (best-seen-rescue at iter=1 ≈ best-seen-rescue at iter=2,
# OP-12 ships the same draft either way). Skip iter 2 to save 5-15 min
# per low-score chapter (30-40% of FastMCP chapters hit this).
NO_RECOVERY_FLOOR = 0.50


# Study orchestrator (Bundle 6 strict-order + Bundle 13 Celery-isolated)
# 2026-05-26 (DD-SYNTH-SPEED-SOTA): bumped 1 → 2. Chapters are API-bound
# (not CPU-bound) on single-node K8s; Bundle 6 streaming already delivers
# chapter 1 at iter-1 wall-time, so per-chapter latency is unchanged but
# study-level throughput doubles. book_harmonize runs AFTER all chapters
# complete so the cross-chapter cache contention is non-issue. Env override
# `KD_STUDY_SEM` rolls back to 1 without redeploy if rotator rate-limits
# saturate.
STUDY_SEM = int(os.environ["KD_STUDY_SEM"])


# Backfill concurrency (vault + corpus_normalize backfills)
# Concurrent build/write per framework. ~8 keeps MinIO+local-CPU saturated
# without flooding (build is parse+hash bound, not I/O).
BACKFILL_CONCURRENCY = 8
