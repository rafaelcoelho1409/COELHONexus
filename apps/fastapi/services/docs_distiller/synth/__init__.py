"""Synth pipeline — chapter markdown generation from planner outputs.

Per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`: 9-substep LangGraph
that consumes `planner/{slug}/plan-latest.json` and emits chapter
markdown via SAWC (stage-parallel best-of-N) + checklist evaluator +
MGSR replan loop. Each node ships independently behind the synth
router's `IMPLEMENTED` tuple, lighting up its card/canvas node in
the FastHTML UI when wired.

Currently shipped:
  - vault.py  (step 5 — byte-exact code-block preservation; the
                first synth node, pure functions, no graph yet)

Shipping next (in dependency order):
  - corpus_normalize (step 2) — ingestion-side hooks
  - outline_sdp      (step 3) — SurveyGen-I PlanEvo single-call outline
  - digest_construct (step 4) — LLMxMapReduce-V3 per-source digest
  - sawc_write       (step 6) — stage-parallel writer + best-of-N
  - checklist_eval   (step 7) — RefineBench binary criteria
  - mgsr_replan      (step 8) — typed replan actions + CoRefine halt
  - render_audit_write (step 9) — Jinja render + round-trip audit
  - cache_lookup     (step 1) — wired last (Redis 30d, partial 7d)
"""
