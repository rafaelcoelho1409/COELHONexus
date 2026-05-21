"""Synth pipeline — chapter markdown generation from planner outputs.

Per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`: 9-substep LangGraph
that consumes `planner/{slug}/plan-latest.json` and emits chapter
markdown via SAWC (stage-parallel best-of-N) + checklist evaluator +
MGSR replan loop. Each node ships independently behind the synth
router's `IMPLEMENTED` tuple, lighting up its card/canvas node in
the FastHTML UI when wired.

Currently shipped (post-2026-05-19 architecture cleanup that moved
`corpus_normalize` + `vault_sentinelize` to ingestion-time and removed
`cache_lookup` in favor of per-stage MinIO content-addressed caching):

  Ingestion-time prep (called from store.py:add_page, NOT a graph node):
    - vault.py            — byte-exact code-block sentinelization
    - corpus_normalize.py — Mintlify/boundary/wrapper-tag stripping

  Synth graph nodes (in nodes/, wired by graph.py):
    - outline_sdp         (step 1) — SurveyGen-I PlanEvo single-call
                                      Structure-Driven Planner — SHIPPED

  Pure libraries:
    - outline.py — Pydantic schemas + DAG primitives + prompt templates
                    + structural validators (consumed by outline_sdp)

  Infrastructure:
    - state.py        — SynthState TypedDict
    - graph.py        — StateGraph builder (IMPLEMENTED-gated)
    - cancel.py       — Redis cancel flag + watcher (mirrors planner)
    - progress.py     — SSE event pub/sub (mirrors planner)
    - observability/  — @traced decorator for OTel spans

Shipping next (in dependency order):
  - digest_construct (step 2) — LLMxMapReduce-V3 per-source digest +
                                 LLM-assigned section routing
  - sawc_write       (step 3) — stage-parallel writer + best-of-N
  - checklist_eval   (step 4) — RefineBench binary criteria
  - mgsr_replan      (step 5) — typed replan actions + CoRefine halt
  - render_audit_write (step 6) — Jinja render + round-trip audit
"""
