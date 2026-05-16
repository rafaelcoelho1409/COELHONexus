# KD Pipeline Sub-Step Map (2026-05-15)

**Purpose:** authoritative enumeration of every observable sub-step in the Knowledge Distiller pipeline. Foundation for the FastHTML per-node observability pages we're building so we stop debugging KD as a black box. Each row identifies a state-change or LLM-call boundary where a panel can render.

**Scope:** ~90 distinct sub-steps across 8 stages. Subagent-derived from source on 2026-05-15 against commit `c27f47f`; trust-but-verify the most-load-bearing line numbers before editing.

**Companion docs:** `docs/KD-DOCKER-QUALITY-FINDINGS-2026-05-15.md` (the problem this work targets), `docs/KD-CANARY-V7-V10-FINDINGS-2026-05-14.md` (architecture state).

---

## Build order

Top-down per user direction (2026-05-15 session):

1. **Resolver + Ingestion** ← starting here
2. Planner (incl. MAP / REDUCE sub-steps)
3. Canary synth
4. Synthesize chapter (incl. Phase A / A.5 / B / C / D + Self-Refine loop)
5. Curator
6. Critic
7. Assembler
8. LLM chain / Bandit (partial — `/admin/rotator/bandit-state` already exists)

Each stage gets: ship the page → use it to inspect defects → ship targeted improvements → move on.

---

## Stage 1 — Resolver + Ingestion

Entry: `POST /api/v1/knowledge/ingestion`. Pre-flight before planner. Tier-specific (1–4).

| # | Sub-step | File | State produced | Persisted | Existing endpoint |
|---|---|---|---|---|---|
| 1.1 | `resolver.lookup` | `services/resolver/sources.py` | `ResolvedStudy {url, tier, language, github_org, github_repo}` | Redis (confidence TTL) | `POST /api/v1/knowledge/resolve` |
| 1.2 | `url_fetch` | `services/knowledge/ingestion.py` + tier-specific (`llms_full_ingest.py`, `llms_txt_ingest.py`, `sitemap_ingest.py`, `tier4_httpx.py`, `github_ingest.py`) | URL list, per-URL HTTP status | MinIO `_cache/ingestion/{fw}/{v}/raw/` | indirect via Celery task meta |
| 1.3 | `scrape_and_chunk` | `services/knowledge/ingestion.py` | per-source `{url, status_code, fetch_ms, chunk_count, truncation_flag, vault_hashes_count}` | MinIO raw/ + monolith | none |
| 1.4 | `dedup_and_sentinelize` | `services/knowledge/ingestion.py:_build_corpus_index` + `_vault_code_blocks` | dedup drop count, vault fence count per file, code-language histogram | MinIO `{study_root}/research/raw/{file}.md` | none |
| 1.5 | `monolith_split` | `services/knowledge/post_ingest.py` | `{orig_filename, split_count, section_count}` | MinIO per-section files | none |
| 1.6 | `vault_emission` | `services/knowledge/ingestion.py:emit_code_vault` | `{sentinel → fence_text, count, language_histogram}` | implicit, carried forward | none |
| 1.7 | `sentinel_audit` | `graphs/knowledge/helpers.py:_audit_sentinel_roundtrip` | audit pass/fail, orphan count, collision list | log only | none |
| 1.8 | `manifest_write` | `services/knowledge/ingestion.py:write_manifest_json` | manifest JSON: per-file stats | MinIO | `GET /api/v1/knowledge/studies/{id}/tree` |

**Already-available instrumentation:** `services/knowledge/ingest_progress.py` writes throttled per-tier progress (`tier, current, total, last_url, status`) to Redis key `coelhonexus:knowledge:ingest_progress:{study_id}` consumed by SSE at `/api/v1/knowledge/studies/{id}/stream`. **Needs extension** to per-URL detail records (status_code, fetch_ms, chunk_count, vault_hashes_count, truncation_flag, error_msg).

---

## Stage 2 — Planner

`graphs/knowledge/distiller.py` planner node, lines 175–678. MAP-REDUCE over shards of ~40 files each. Memory `project_planner_map_replacement.md` notes a deterministic-classical opt-in (`KD_USE_CLASSICAL_MAP=1`).

| # | Sub-step | File:line | State | Persisted | Endpoint |
|---|---|---|---|---|---|
| 2.1 | `corpus_load` | distiller.py:201 | `entries = [(slug, content), ...]` | implicit (MinIO read) | none |
| 2.2 | `off_topic_filter` | distiller.py:204–216 | embedding cosine drop count (<0.30) | log only | none |
| 2.3 | `code_aware_dedup` | distiller.py:217–229 | drop count (>85% prose + same code blocks) | log only | none |
| 2.4 | `manifest_hash_compute` | distiller.py:231 | manifest_hash (32-hex) | implicit (cache key) | none |
| 2.5 | `cache_lookup` | distiller.py:234 | hit/miss + cached_at | Redis (~30d TTL) | none |
| 2.6 | `shard_creation` | distiller.py:275–281 | shards (≤40 files) | implicit | none |
| 2.7 | **MAP.label_shard** (per shard, parallel, sem=22) | distiller.py:325–454 | `ShardLabels {clusters, unused_shard_slugs}` | Langfuse tag `planner-shard-{idx}` | `GET /api/v1/knowledge/debug/map_compare` (classical vs LLM) |
| 2.7a | `map.strict_json_path` | distiller.py:332–363 | strict JSON-Schema enum decode success/fail | Langfuse | none |
| 2.7b | `map.fallback_function_calling` | distiller.py:372–409 | function_calling + post-hoc filter result | Langfuse | none |
| 2.7c | `map.slug_coverage_repair` | distiller.py:431–446 | auto-park dropped/hallucinated slugs | log only | none |
| 2.7d | `map.truncation_guard` | distiller.py:447–453 | finish_reason length-truncation flag | log only | none |
| 2.8 | `map.shard_semaphore_bounded` | distiller.py:510–545 | timeout expiration → synthetic "timed-out" cluster | log only | none |
| 2.9 | `map_complete` | distiller.py:547–571 | total clusters, total unused, elapsed | log only | none |
| 2.10 | **REDUCE.clio_pattern** | distiller.py:591–602 (`embed_and_cluster_reduce`) | `ChapterPlanList {chapters, unused_files, reasoning}` | Langfuse per label call | none |
| 2.10a | `reduce.embed_and_cluster` | within reduce | embeddings + k-means assignments | implicit | none |
| 2.10b | `reduce.label_meta_clusters` | within reduce | per-cluster LLM-labeled name + goal | Langfuse | none |
| 2.11 | `plan_validation` | distiller.py:608–611 | missing/orphan/hallucinated warnings (non-gating) | log only | none |
| 2.12 | `coverage_invariant_repair` | distiller.py:613–660 | orphan count, hallucinated count | log only | none |
| 2.13 | `write_plan_json` | distiller.py:663 | plan_key, chapter count, reasoning excerpt | MinIO `{study_root}/research/plan.json` | `GET /tree` |
| 2.14 | `cache_write` | distiller.py:670–673 | best-effort write | Redis `_cache/planning/{fw}/{v}/` | none |

---

## Stage 3 — Canary synth

`distiller.py:canary_synth` lines 2704–2901. Single-chapter smoke test before fan-out.

| # | Sub-step | State | Persisted |
|---|---|---|---|
| 3.1 | `pick_canary_chapter` | first chapter in plan | implicit |
| 3.2 | `synthesize_attempt` | ChapterOutput, tokens, latency | Langfuse |
| 3.3 | `grader_attempt` | `GraderEvaluation {score, issues}` | Langfuse |
| 3.4 | `gating_decision` | gate decision, reason (soft gate) | log only |

---

## Stage 4 — Synthesize chapter

`distiller.py:synthesize_chapter` lines 683–1983. Parallel per-chapter Self-Refine loop (max 5 iters).

| # | Sub-step | File:line | Notes |
|---|---|---|---|
| 4.1 | `cache_lookup` | distiller.py:734–741 | (fw, ver, profile_hash, ch_num, title, files) keyspace |
| 4.2 | `tone_profile_build` | distiller.py:778 | injected into every LLM prompt |
| 4.3 | `file_load_and_rank` | distiller.py:779–782 | BM25 rank by chapter goal |
| 4.4 | `vault_code_blocks` | distiller.py:788 | extract fences, replace with `<code-ref hash="..."/>` |
| 4.5 | `vault_diagnostic_logging` | distiller.py:789–807 | language histogram, chars before/after |
| 4.6 | `chapter_model_pin` | distiller.py:820–854 | bandit picks one LM for all Phase A/C + refine iters |
| 4.7 | `prose_only_short_circuit` | distiller.py:856–967 (OP-46) | when vault empty — skips audit + Self-Refine |
| 4.8 | **Self-Refine loop** | distiller.py:969–1970 | iterates 4.8a–4.8l until accept or max-iter |
| 4.8a | `synthesize_attempt` | distiller.py:1087+ | `ChapterOutput {sections, challenges, flashcards}` |
| 4.8b | `vault_restoration` | distiller.py:1090+ | sentinel → original fence byte-exact |
| 4.8c | `hierarchical_vs_monolithic_decision` | distiller.py:1093+ | vault ≥50 hashes → hierarchical |
| 4.8d.A | **Phase A — outline** | `hierarchical_synth.py:103` | `ChapterOutline {sections, challenges, flashcards}`. Optional classical via `KD_USE_CLASSICAL_OUTLINE=1` |
| 4.8d.A5 | **Phase A.5 — bucket_split** | `hierarchical_synth.py:441` | sections w/ >10 hashes → k-means sub-sections |
| 4.8d.B | **Phase B — hash_routing** | `hierarchical_synth.py:299` | cosine assign hashes to sections; `shared_core` (high-entropy); orphan rate |
| 4.8d.C | **Phase C — parallel section_synth** | `helpers.py:1698+` | asyncio.gather, `_PHASE_C_CONCURRENCY=8` |
| 4.8d.D | **Phase D — merge** | hierarchical_synth.py | concat Section drafts into ChapterOutput — **no heading-dedup step exists** (defect identified in `KD-DOCKER-QUALITY-FINDINGS-2026-05-15.md` item C) |
| 4.8e | `audit_sentinel_roundtrip` | distiller.py:1118+ | `{n_missing, n_invented, n_empty, n_duplicate, n_thin}` |
| 4.8f | `audit_structured_output_refs` | distiller.py:1119+ | code_refs array consistency |
| 4.8g | `code_syntax_score` | distiller.py:1120+ | Python AST parse check |
| 4.8h | `assemble_chapter_markdown` | distiller.py:1121+ | calls `helpers.py:854` (+ `_scrub_assembled_markdown` 9 passes at line 626) |
| 4.8i | `grade_attempt` | distiller.py:1140+ | `{action: accept|refine|regenerate, score, issues}` |
| 4.8j | `decision_logic` | distiller.py:1180+ | best-seen tracking (OP-12) + audit-regression early-stop (OP-7, 1.2× ratio) |
| 4.8k | `generate_adjustment` | distiller.py:1200+ | targeted feedback for next iter |
| 4.8l | `regression_tracking` | distiller.py:985–1020 | best_synthesis, best_eval, prev_n_issues |
| 4.9 | `write_chapter_artifacts` | distiller.py:1217+ | 3 MinIO objects (README, challenges, flashcards) |
| 4.10 | `cache_write` | distiller.py:1220–1225 | Redis if score ≥ threshold |

---

## Stage 5 — Curator

`distiller.py:curator` lines 1987–2246. Cross-chapter quality flags.

| # | Sub-step | File:line | Notes |
|---|---|---|---|
| 5.1 | `load_all_chapters` | distiller.py:2003 | MinIO read |
| 5.2 | `curate_one` per chapter | distiller.py:2039+ | LLM call → `{is_rigorous, tone_consistent, well_structured}` |
| 5.3 | `curation_summary` | distiller.py:2100+ | aggregate flags |

---

## Stage 6 — Critic

`distiller.py:critic` lines 2247–2574. Holistic audit + DEBT.

| # | Sub-step | File:line | Notes |
|---|---|---|---|
| 6.1 | `load_chapter_previews` | distiller.py:2264 | 10k char truncation |
| 6.2 | `scan_citations` | distiller.py:2265 | regex `# docs:` count |
| 6.3 | `scan_hallucinated_fences` | distiller.py:2266 | unresolved sentinel detector |
| 6.4 | `critic_llm_call` | distiller.py:2352+ | `CriticAssessment {accept, issues}` |
| 6.5 | `debt_assembly` | distiller.py:2268 | DEBT.md in MinIO |
| 6.6 | `critic_decision` | distiller.py:2300+ | soft gate |

---

## Stage 7 — Assembler

`distiller.py:assembler` lines 2575–2703. Final index + glossary + episodic.

| # | Sub-step | File:line | Notes |
|---|---|---|---|
| 7.1 | `load_all_chapters` | distiller.py:2599 | MinIO read |
| 7.2 | `build_chapter_bundles` | distiller.py:2600 | 500-char previews |
| 7.3 | `deterministic_linter` | distiller.py:2601 | regex lint, no LLM |
| 7.4 | `extract_glossary_terms` | distiller.py:2602 | regex + 1 LLM call → glossary.json |
| 7.5 | `build_chapter_summaries` | distiller.py:2603 | per-chapter one-liner |
| 7.6 | `call_assembler_llm` | distiller.py:2604 | index.md (ToC + cross-refs) |
| 7.7 | `log_episodic_memory` | distiller.py:2605 | Langfuse long-term learning |
| 7.8 | `write_manifest` | distiller.py:2607 | final manifest.json |

---

## Stage 8 — LLM chain + bandit

| # | Sub-step | File:line | Endpoint |
|---|---|---|---|
| 8.1 | `discovery_fan_out` | discovery.py:303 (`list_all_alive_models`) | `GET /api/v1/admin/rotator/models` |
| 8.2 | `model_ranking` | benchmarks.py (`rank_for_step`) | `GET /api/v1/admin/rotator/ranked?step=...` |
| 8.3 | `bandit_predict` | services/pareto_bandit.py (`predict`) | `GET /api/v1/admin/rotator/bandit-state` (introspection) |
| 8.4 | `bandit_update` | services/pareto_bandit.py (`update`) | OTel counter `kd.pareto_update_total{outcome}` |

---

## FastHTML page convention

Established pattern in `apps/fasthtml/routes/kd.py` (verified):

- **Shell page**: `@ar("/kd/<thing>")` returns a component shell with empty placeholders
- **HTMX fragment**: `@ar("/api/kd/<thing>/<part>")` returns rendered HTML, swapped into the shell on polling interval (typical 5–15s) or SSE
- **Reverse proxy**: `@ar("/api/kd/inspect/{rest:path}")` style forwards method/body/query to FastAPI `/api/v1/knowledge/...`
- **Data source**: existing FastAPI endpoints when possible; new endpoints when not
- **Components**: live in `apps/fasthtml/components/kd_*.py`

### Stage 1 (Resolver + Ingestion) — proposed wiring

| Layer | Path | Purpose |
|---|---|---|
| Shell page | `GET /kd/studies/{study_id}/observability/ingestion` | full-page layout, HTMX placeholders |
| Fragment poll | `GET /api/kd/studies/{study_id}/observability/ingestion/fragment` | re-render every 2–3 s |
| Data source A (already exists) | `GET /api/v1/knowledge/studies/{id}/stream` (SSE) | per-tier progress (`tier, current, total, last_url, status`) from `ingest_progress.py` |
| Data source B (needs work) | per-URL detail records (status_code, fetch_ms, chunks, vault_hashes, truncation) | **extend** `IngestProgress` with `record_url(url, **stats)` writing a Redis list; expose via new `GET /api/v1/knowledge/studies/{id}/observability/ingestion` |
| Components | `apps/fasthtml/components/kd_observability_ingestion.py` (new) | header card + per-URL table + per-tier summary |

### Proposed Stage 1 columns (per-URL row)

| Column | Source | Type | Use |
|---|---|---|---|
| `url` | scraper | string (truncated link) | identify source |
| `status` | scraper | enum: pending / fetching / success / 4xx / 5xx / timeout | colored chip |
| `http_code` | scraper | int | precision |
| `fetch_ms` | scraper | int | latency outliers |
| `chunks` | chunker | int | how the chunker split the file |
| `truncation` | chunker | bool | flag mid-command code blocks |
| `code_fences` | vault emission | int | code density |
| `vault_hashes` | vault emission | int | unique sentinels emitted |
| `code_langs` | vault emission | csv string | language mix |
| `dedup` | dedup pass | enum: kept / dropped_dup | what dedup did |
| `off_topic` | filter | bool | embedding cosine drop |
| `chars_raw` / `chars_vaulted` | pre/post vault | int | sentinelization compression |
| `error_msg` | scraper | string | failure reason |

### Stage 1 header widgets

- progress bar: `fetched / total` per tier
- error rate %: timeouts, 5xx, 4xx per provider
- vault totals: code fences, unique hashes, avg hashes/file
- start time, elapsed, ETA (linear regression of throughput)

---

## Quality-fix linkage

The defects logged in `docs/KD-DOCKER-QUALITY-FINDINGS-2026-05-15.md` map to specific sub-steps as follows. The observability pages should surface the metric that would have caught each one in real time:

| Defect (from quality doc) | Sub-step | Metric the page should show |
|---|---|---|
| `# docs:` source-ID leakage | 4.8d.A, 4.8d.C, 4.8h | per-section `prose_md` regex count of `# docs:` markers |
| `<code-ref/>` unresolved | 4.8d.B (vault routing) → 4.8h (assembler:902–906) | per-chapter unresolved sentinel count (currently silently skipped) |
| Orphan hex hashes | 4.8d.C, 4.8h | scrubber Pass-10 candidate count (proposed) |
| `(truncated)` markers | 1.3 (scrape/chunk), 4.8h Pass-8 | per-URL `truncation` bool + per-chapter `truncated_blocks_repaired` |
| Duplicate H2 sections | 4.8d.D (no dedup) | per-chapter section-heading hash collisions |
| Stub placeholders | 4.8a–c (failed structured output) | per-chapter `challenges.md` size + chapter-keyword overlap |
| Ch02 mis-routing | 2.10b (reduce.label_meta_clusters) | per-chapter title-coherence score vs assigned files |
| 3 missing chapters | 4.8 not reached for ch06/09/10 | per-chapter task state (pending / running / accepted / debt / not_started) |

---

## Next-session pickup

Start tasks in order:

1. Verify subagent line numbers in `discovery.py`, `ingestion.py`, `resolver/sources.py` (this doc lists claimed lines — confirm with `grep -n`).
2. Map exact FastHTML scaffolding (routes registered in `apps/fasthtml/main.py`; sidebar entries in `components/sidebar.py`).
3. Extend `services/knowledge/ingest_progress.py` with `record_url(url, **stats)` writing a capped Redis list (e.g. last 500 records via `LTRIM 0 499`).
4. Add FastAPI endpoint `GET /api/v1/knowledge/studies/{id}/observability/ingestion` returning the list + header summary as JSON.
5. Build FastHTML shell + HTMX fragment + component for the page.
6. Wire ingest callers (`llms_full_ingest.py`, `llms_txt_ingest.py`, `sitemap_ingest.py`, `tier4_httpx.py`, `github_ingest.py`) to call `record_url` after each successful or failed fetch.
7. Skaffold redeploy. Run a Docker study. Watch the page. Iterate.

Once Stage 1 lands, move to Stage 2 (Planner) using the same pattern.

---

## Cross-references

- `docs/KD-DOCKER-QUALITY-FINDINGS-2026-05-15.md` — defects this observability work targets
- `docs/KD-CANARY-V7-V10-FINDINGS-2026-05-14.md` — architecture state
- `docs/KD-SPEED-OPTIMIZATION-PLAN-2026-05-14.md` — already-shipped speed batches
- `apps/fastapi/services/knowledge/ingest_progress.py` — existing throttled progress reporter (extend this)
- `apps/fastapi/routers/v1/knowledge/distiller.py::stream_study` — SSE consumer (extend its emitted shape)
- `apps/fasthtml/routes/kd.py` — route pattern
- `apps/fasthtml/components/kd_studies.py` — component pattern (especially `ChaptersListFragment` for polling-w/-state-preservation)
- Memory `project_planner_map_replacement.md` — classical-MAP rationale
- Memory `feedback_kd_quality_over_speed.md` — quality > wall-time
