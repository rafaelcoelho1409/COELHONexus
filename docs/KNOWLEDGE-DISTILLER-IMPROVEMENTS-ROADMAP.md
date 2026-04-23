# Knowledge Distiller — Improvements Roadmap

Dated: 2026-04-22

Synthesis of a full architecture + code audit against the end-to-end run
completed on 2026-04-22 (v13, first successful full-pipeline test with
NIM-embeddings + v2 Clio REDUCE). Organized by impact × effort with
quality-preservation property called out per item.

---

## Context from the 2026-04-22 run

The run that this roadmap is built against:

| Stage | Wall-clock | Notes |
|---|---|---|
| Ingest (Tier 1 llms-full.txt, 10 MB) | ~5 s | Fast-path hit |
| Splitter (4088 CommonMark sections + MinIO writes) | ~5 min | Chunk-retry handled transient `IncompleteBody` correctly |
| MAP (103 shards × ≤40 files, parallel LLM calls) | ~16 min | Several shards cascaded through 1-2 NIM 504 timeouts before landing on working models — ~63 of 103 shards stuck at primary for full 300s gateway window |
| REDUCE v2 (NIM embed → UMAP → KMeansConstrained → labels → order) | ~7.5 min | **Works.** k=9 balanced, silhouette **0.470**, one slug dedup. UMAP 2048d→5d took 303s (5 min; dominant cost). |
| Synth (first 2 chapters) | ~13 min each | Self-Refine loop working cleanly; score=0.71-0.75 → adjustment → retry |
| Synth (chapter 3) | **Failed** | All 12 synth fallback models returned None/malformed output on a 180K-char (45K-token) prompt. `phase=failed` sentinel recorded. Also affected by external Obsidian sync deleting MinIO files mid-run (not a KD bug). |
| Curator / Critic / Assembler | n/a | Did not reach |

**Ingredient-level root causes observed:**

1. NIM's chat-completions endpoint returns 504 Gateway Timeout at ~300s on reasoning-model calls with large prompts.
2. Groq's 413 is a TPM rate-limit, not a context-window issue. Only `meta-llama/llama-4-scout-17b-16e-instruct` (30K TPM) fits 25-35K prompts.
3. `CHAPTER_FILES_MAX_CHARS = 180_000` (~45K tokens) pushes every synth call into the danger zone.
4. MAP fires 103 shards via `asyncio.gather` without concurrency cap — NIM's 40 RPM means 63+ requests queue at primary and hit the gateway timeout.
5. UMAP runtime scales ~linearly with input dim — 2048d is slow. PCA preprocessing would cut 5 min to 30 s.

**What already works well (don't touch):**

- Clio v2 REDUCE (UMAP + KMeansConstrained + CH tiebreaker + slug dedup) — silhouette jumped 0.063 → 0.470
- NIM embedding endpoint (`nvidia/llama-nemotron-embed-1b-v2`) is separate from the chat endpoint and fully reliable
- LLM fallback chain research is current as of 2026-04-20; no model-list changes needed
- Coverage-repair in `distiller.py` lines 546-579 handles orphans + hallucinations correctly
- Cache layer (plan.json keyed by `manifest_hash`, ingest by framework+version)

---

## Tier 1 — Top 5 wins (ship this week, 1-2 days combined)

All quality-neutral-to-positive, all low LoC.

### 1. BM25 file-ranking before synth truncation — RELIABILITY + QUALITY
**Current:** chronological order, truncate at `CHAPTER_FILES_MAX_CHARS=180K`. Chapters with 100+ files lose the alphabetically-late ones regardless of relevance.
**Proposed:** BM25 rank the chapter's files against `chapter.goal` string, take top-N until budget.
**Effect:** most pedagogically relevant files always make it into the synth prompt, not the truncation tail.
**Files:** `graphs/knowledge/helpers.py` — add `_rank_chapter_files()`, call it from `_load_chapter_files()`.
**Effort:** ~50 LoC. scikit-learn already present.

### 2. Lower `CHAPTER_FILES_MAX_CHARS` 180K → 80K — RELIABILITY (eliminates cascade)
**Current:** 45K-token synth prompts → NIM reasoning models hit 300s gateway → Groq 413 on all but llama-4-scout.
**Proposed:** 20K-token synth prompts — fits every Groq model's TPM, NIM non-reasoning completes in <60s.
**Quality:** unchanged given #1 is shipped (only the most relevant 80K chars remain).
**Files:** `graphs/knowledge/helpers.py` — one constant.
**Effort:** 1 LoC change.

### 3. PCA pre-reduction before UMAP — SPEED (zero quality loss)
**Current:** UMAP 2048d → 5d takes 303s (biggest REDUCE cost today).
**Proposed:** `PCA(n_components=128)` → UMAP 128d → 5d, finishes in ~30s total.
**Quality:** PCA retains 99%+ variance on sentence-transformer embeddings; UMAP output identical within noise.
**Files:** `graphs/knowledge/reduce_cluster.py` — add PCA step before UMAP.
**Effort:** ~10 LoC.

### 4. MAP inter-shard concurrency cap — RELIABILITY + SPEED
**Current:** 103 shards fire simultaneously via `asyncio.gather`. NIM 40 RPM means 63+ stuck at 300s gateway.
**Proposed:** `asyncio.Semaphore(30)` wrapping the shard gather. Primary completes before pressure builds.
**Effect:** MAP 16 min → ~10 min, 504s in logs drop materially.
**Files:** `graphs/knowledge/distiller.py` — wrap `_label_shard` call site.
**Effort:** ~5 LoC.

### 5. Per-shard / per-synth eager timeout — SPEED
**Current:** each LLM call waits NIM's full 300s gateway before `with_fallbacks` tries next model.
**Proposed:** `asyncio.wait_for(chain.ainvoke(...), timeout=120)` — cascade at 2 min.
**Quality:** unchanged (the call wasn't going to return anyway).
**Files:** every `ainvoke` site where the LLM is a `RunnableWithFallbacks` and the prompt is large.
**Effort:** ~5 LoC per site.

**Combined expected effect on the 2026-04-22 run baseline:**
- Chapter 3 synth failure: eliminated
- Total pipeline wall-clock: ~40 min instead of 90+
- No quality loss (potentially small quality gain from BM25 ranking)

---

## Tier 2 — Quality-positive wins (2-3 days, sprint 2)

### 6. MinHash file dedup at synth — QUALITY (removes duplicate content)
Many files in a chapter overlap (`/api/x.md` + `/api/x/reference.md` describe the same API). MinHash + Jaccard >0.7 → merge near-duplicates before LLM. Cleaner output, smaller prompt, no content loss. ~80 LoC. `datasketch` or `mmh3`.

### 7. TF-IDF glossary extraction across all chapters — CURATOR QUALITY
**Current:** heuristic Counter over chapter-0 CamelCase/snake_case → misses vocabulary from later chapters.
**Proposed:** `TfidfVectorizer` across all chapters → top-12 domain-specific terms reliably.
**Effect:** curator normalizes terminology consistently across the full study.
**Files:** `graphs/knowledge/helpers.py` — replace `_extract_glossary_terms()`.
**Effort:** ~15 LoC.

### 8. Parallel curator over chapters — SPEED
**Current:** curator is sequential per chapter (~10 min for 9 chapters on GLM-5.1).
**Proposed:** `asyncio.Semaphore(2)` — 2 chapters curated concurrently. Fits GLM-5.1's rate limit.
**Effect:** curator 10 min → ~5 min.
**Effort:** ~10 LoC.

### 9. Deterministic pre-gates on grader — SPEED (zero quality loss)
**Current:** grader LLM runs for every Self-Refine iteration.
**Proposed:** pre-compute Flesch-Kincaid, code-block ratio, citation density, heading sanity. Any failure below threshold → reject without calling grader LLM.
**Effect:** ~50% of iter-0 chapters fail a cheap threshold and skip the LLM call → immediate refine.
**Files:** `graphs/knowledge/helpers.py` — new `_deterministic_grader_gates()`.
**Effort:** ~30 LoC.

### 10. Citation-regex whitelist in critic — QUALITY (eliminates false positives)
**Current:** critic's `_CITATION_RE = r"#\s*docs:\s*([^\s\n`)]+)"` captures slug-like patterns including non-slugs (e.g., `api(utils)` captures `api`).
**Proposed:** build `|`-joined alternation of actual corpus slugs at preprocess time; regex only matches real slugs.
**Effect:** zero false positives in citation integrity check.
**Files:** `graphs/knowledge/helpers.py` — rewrite `_scan_citations()`.
**Effort:** ~10 LoC.

---

## Tier 3 — Architectural changes (defer until bottleneck shifts)

### 11. Hybrid MAP (Clio-at-shard-level)
Embed files per shard + classical cluster (k=3 per shard) + LLM names only. Trades 103 complex calls for 309 tiny calls + 4088 embeddings. **Defer until MAP is actually the pipeline bottleneck** — after Tier 1 #4 + #5, MAP runs in ~10 min which is acceptable. See `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` for the pattern. ~200 LoC.

### 12. Sub-chapter synthesis batching
Split thick chapters into groups of ~20 files, synthesize each group, merge. Preserves quality at ANY chapter size without truncation. **Deferred** because Tier 1 #1 + #2 eliminate today's failure mode more cheaply. Revisit if chapters legitimately exceed 80K chars of relevant content. ~150 LoC.

### 13. Per-chapter artifact cache (resume-on-failure)
When chapter 7 fails, don't re-synth chapters 1-6. Store `{study_id, chapter_n} → synthesis_output` in MinIO; synth node checks before calling LLM. Robust against transient failures (Obsidian-style external deletions, NIM outages mid-run). ~100 LoC.

### 14. LangFuse observability (self-hosted)
Every LLM call as a span with model/tokens/cost/latency/status. Today's run would have been debugged visually in minutes instead of grepping HTTP codes. Also gives prompt versioning + session replay for free. ~50 LoC + docker-compose sidecar. See `openletemetry-and-ai-agents-guide.md` for context on observability stack.

### 15. Qdrant cross-study semantic search
Once N studies exist, index every chapter content via `fastembed`. Enables "which study explains LangGraph state schemas?" via vector search — no LLM needed for retrieval. Sets up the shared infra pattern for Book Distiller + Deep Research. ~200 LoC.

---

## Tier 4 — Strategic / future

### 16. Preview mode (classical-only baseline)
A `?preview=true` parameter runs: ingest → splitter → MAP clustering → c-TF-IDF cluster labels → TextRank per-chapter extractive summaries. No synthesis, no challenges, no flashcards. ~5 min wall-clock, zero LLM cost. Usable as:
- Sanity-check before committing to a full 30-min run
- Fallback when all LLM providers are down
- Validation baseline to catch synth hallucinations

~150 LoC.

### 17. Noise pre-filter before MAP
Classical heuristics (slug pattern matching: `changelog`, `release-notes`; file length < 200 chars; code-to-prose ratio ≈ 0) drop obvious noise before shard-labelers waste LLM calls. Typical effect: 5-15% fewer shards. ~30 LoC.

### 18. Grader hard-threshold per dimension
If `citation_integrity < 0.5`, skip scoring the other 7 dimensions — straight to refine. Fast-fail the hopeless cases. ~20 LoC. Fits alongside #9.

---

## Suggested sprint order

**Sprint 1 (1-2 days, ship this week):**
#1 + #2 + #3 + #4 + #5 + #10.

Eliminates today's failure modes (chapter 3 synth blowup, UMAP 5 min, MAP stampede), cuts wall-clock ~40%, quality-neutral to slightly positive from #1 + #10.

**Sprint 2 (2-3 days):**
#6 + #7 + #8 + #9.

Quality-positive polish. No architectural risk.

**Sprint 3 (when ready):**
#14 (LangFuse) first. Observability first — it makes all subsequent tuning 10× easier, and you see regressions immediately instead of chasing them in logs. Then #12 or #13 depending on what failure patterns actually persist.

**Strategic:**
#15 + #16 — both establish shared infrastructure reusable by Book Distiller + Deep Research.

---

## Deliberately not in the list

- **Changing the LLM fallback chain.** Research-tuned 2026-04-20 (`llm_chain.py` header). Model list is current.
- **Rewriting synth prompts.** Well-calibrated. Tune truncation + ranking instead.
- **Replacing KMeansConstrained with HDBSCAN.** Clio explicitly rejected this (Appendix G.7) for same-domain corpora — dumps 50%+ points to noise without heavy patching. Same reasoning applies here.
- **Replacing the critic.** Cheap, already mostly deterministic, works.
- **Adding MLflow.** We're not training or versioning models. Logger output is sufficient observability for classical-ML diagnostics.
- **Switching to raw OpenTelemetry first.** For LLM-heavy workloads, LangFuse is the faster win; OpenLLMetry on top comes later for infra-wide tracing.

---

## References

- `KNOWLEDGE-DISTILLER-ARCHITECTURE.md` — canonical architecture
- `KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md` — Tier 1-4 ingestion strategy
- `KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md` — framework-to-URL resolver
- `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` — the v2 Clio REDUCE doc
- `STUDY-GENERATOR-ADAPTIVE-GRADER.md` — grader design + Self-Refine details
- `KNOWLEDGE-DISTILLER-ROUTER-SPLIT.md` — API surface
- Clio paper (Anthropic, arXiv 2412.13678) — Appendix G.5, G.7
- BERTopic Best Practices — https://maartengr.github.io/BERTopic/getting_started/best_practices/best_practices.html
- k-means-constrained — https://github.com/joshlk/k-means-constrained
- HERCULES (hierarchical k-means + LLM) — https://arxiv.org/abs/2506.19992
