# KD Planner Step-by-Step Analysis (2026-05-15)

**Subject:** per-sub-step deep analysis of the planner stage based on the **LiteLLM v3 study** (`study_id=19b7b667-8f0f-4caa-ab9c-1754efc8317a`), the first real production data we have through the Stage 2 observability stack. Companion to `docs/KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md` (which catalogs the sub-steps) and `docs/KD-DOCKER-QUALITY-FINDINGS-2026-05-15.md` (which catalogs the problems the observability work targets).

**Purpose:** turn raw observability numbers into a ranked, file:line-mapped fix queue. We'll iterate through this list one fix at a time; each fix gets a re-run against the same LiteLLM corpus so its effect is measurable.

**Headline:** the new Stage 2 viz surfaced one RED chapter (`Ch6 'Litellm Logging Guardrail Integrations and Related'` at coherence 0.293, well below the 0.35 red threshold) plus 6 YELLOW chapters. The same slug families (`complete-git-diff`, `general-proxy-improvements`, `release-notes`, `contact-us`) show up in multiple chapters' worst-coherence lists, indicating *structural* off-topic content that survives the upstream filter — exactly the pattern the docker findings had at chapter-synthesis time.

---

## Run context

- **Framework**: LiteLLM (`docs.litellm.ai/llms-full.txt`, Tier 1)
- **study_id**: `19b7b667-8f0f-4caa-ab9c-1754efc8317a`
- **study_root**: `obs-planner-v3/knowledge/litellm-latest-senior`
- **Wall-clock end-to-end**: 187.2 s
- **Caches**: ingest cache hit (cache-restore path fired, copied 776 files), plan cache miss (full MAP+REDUCE ran)
- **MAP path used**: `classical_map` (13/13 shards), per the `KD_USE_CLASSICAL_MAP=1` default in helm `values.yaml` (commit `6eee1d2`)
- **Result**: 8 chapters, 0 orphans, 0 hallucinated, validation clean

---

## 2.1 — Corpus load

| Signal | Value |
|---|---:|
| Files | 776 |
| Total bytes | 547,861 |
| Load time | 16,494 ms (~47 files/s) |
| **Byte spread** | **min=8, median=460, max=9296 — 1162× max/min** |

**Defect**: 8-byte files are effectively empty (a few characters of post-split heading text with no body). The monolith-split step at `services/knowledge/post_ingest.py:_split_markdown_by_headings` emits these stub sections downstream where they (a) consume embed budget and (b) trigger spurious off-topic/dedup decisions.

**Fix**: in `services/knowledge/post_ingest.py:split_monolith_if_needed`, between Phase 1 slug-build and Phase 2 MinIO write, skip any section whose body is below `_MIN_SECTION_BYTES = 64` (or similar).

---

## 2.2 — Off-topic filter ← **biggest problem at this step**

| Signal | Value |
|---|---:|
| Threshold | 0.30 |
| Kept / dropped | 605 / 171 |
| **Boundary (±0.05 of threshold)** | **373 of 776 (48%)** — 233 barely-kept, 140 barely-dropped |
| Cosine distribution | p10=0.271, p50=**0.348**, p90=0.434 |
| Domain coherence | 0.722 |

**Diagnosis**: the **threshold sits on the slope** of the distribution, not at a valley. Half the corpus has cosine ≤ 0.348, so a static 0.30 cutoff is fragile.

**Concrete false positives** verified by per-file inspection (these are kept/dropped at cos≈0.300 — single decimal points either side):

| Boundary kept (false-positive keep — looks like teaching but is changelog) | cos |
|---|---:|
| `0614-...-management-endpoints-ui-improvements` | 0.300 |
| `0396-...-security-https-...-release-` | 0.301 |
| `0444-...-helm-improvements` | 0.301 |
| `0160-...-spend-tracking-improvements` | 0.301 |

| Boundary dropped (false-positive drop — real LiteLLM teaching) | cos |
|---|---:|
| `0292-...-general-proxy-improvements` | 0.300 |
| `0548-...-management-endpoints-updates` | 0.300 |
| `0385-...-assigning-team-admins` | 0.300 |
| `0327-...-llm-translation-...-release-` | 0.299 |

**Improvements ranked**:

1. **Lower threshold 0.30 → 0.27** at `apps/fastapi/graphs/knowledge/helpers.py:1000` (`_OFF_TOPIC_THRESHOLD = 0.30`) — recovers ~140 false-positive drops including `assigning-team-admins`, `llm-translation`. Marginal cost: ~20 weak keeps survive that would've been dropped.

2. **Two-step filter** — after the prototype-anchor pass, compute the **kept-set centroid** and re-score the *dropped* files against it; restore any with cos(centroid) ≥ 0.35. Catches "this isn't like the abstract prototype but IS like the other docs of this framework". `_filter_off_topic_files` already exposes `domain_coherence` via the diag dict; the centroid can be recomputed in one line.

3. **Structural deny-list at slug-level** — these patterns dominate every chapter's worst-coherence list and should be dropped regardless of cosine: `release-notes`, `complete-git-diff`, `git-diff`, `contact-us`, `b-https-` (broken-link slug fragments). Add as `_OFF_TOPIC_SLUG_PATTERNS` constant + check at start of `_filter_off_topic_files`.

---

## 2.3 — Code-aware dedup

| Signal | Value |
|---|---:|
| Threshold | Jaccard ≥ 0.85 |
| Pairs checked | 156,463 |
| Dropped | 99 |
| Jaccard distribution | min=0.852, median=0.906, **max=1.000** |

**Defect**: 5+ pairs at Jaccard=1.000 with Δbytes=0 are the **same monolith section under different ordinals**:

```
KEPT 0053-docs-litellm-ai-llms-full-demo-instance-https
drop 0136-docs-litellm-ai-llms-full-demo-instance-https  Δbytes=+0
drop 0147-docs-litellm-ai-llms-full-demo-instance-https  Δbytes=+0
drop 0157-docs-litellm-ai-llms-full-demo-instance-https  Δbytes=+0
drop 0167-docs-litellm-ai-llms-full-demo-instance-https  Δbytes=+0
```

The post-ingest split is emitting the same content under 5 ordinals. The downstream dedup catches them after the fact, but they've already consumed embed budget and the duplicate ordinals corrupt cluster centroids before the dedup runs.

**Fix at source**: in `apps/fastapi/services/knowledge/post_ingest.py:split_monolith_if_needed`, between Phase 1 (slug+body build) and Phase 2 (MinIO write), dedup `writes` by `sha256(body)` — keep the first occurrence.

---

## 2.5 — Plan cache lookup

| Signal | Value |
|---|---|
| Manifest hash | `4d0e318848d6b4a2` |
| Hit | False (as designed for this validation run) |

No action — working as intended. Worth instrumenting `cached_at` age in seconds once we have hits to compare against.

---

## 2.6 + 2.7 — Shard creation + Classical MAP

| Signal | Value |
|---|---:|
| Shards | 13 × ≤40 files |
| Shard_results | 13/13 `classical_map` path ✓ |
| Total clusters across shards | 42 |
| Total unused slugs | 18 |
| Cluster size distribution | min=2, p25=2, median=3, p75=28, **max=37** — bimodal |
| Generic-name clusters | **3/42** |

**Generic-name examples**:
- shard 1: `Litellm Overview` (27 files)
- shard 1: `Helicone Platform Documentation` (2 files)
- shard 10: `General Proxy Enhancements` (31 files)

**Diagnoses**:
- **Bimodal cluster sizes** — half the clusters have 2-3 files (`community_detection` noise), the other half are 25+ (real topics). The 2-file clusters add little signal to REDUCE.
- **Generic labels** — KeyLLM is producing nominal/junk-drawer names for the bigger clusters (`Litellm Overview`, `General Proxy Enhancements`). These propagate up to chapter titles and hurt coherence.
- **Competitor mention** — `Helicone Platform Documentation` cluster is named after a *competing* observability platform that LiteLLM mentions in 2 files. These passed off-topic (cos ≥ 0.30 because they share concepts with LiteLLM) but they're not really LiteLLM teaching content.

**Improvements**:

1. **Raise `min_size`** in `classical_map.py`'s community_detection from `2 → 3` — eliminates 2-file noise clusters; their slugs flow into `unused_shard_slugs` which REDUCE handles cleanly. Eliminates ~8-12 micro-clusters per run.

2. **Generic-name re-labeling pass** — regex-check each `cluster_name` against `\b(overview|general|documentation|misc|other|enhancements|features|updates)\b`; if matched, re-prompt KeyLLM with a "name the SPECIFIC technical topic shared by these files" hint. Could be one extra call per generic label.

3. **Framework-specific competitor filter** (low priority) — extend off-topic deny-list with names that match `\b(helicone|langfuse|datadog)\b` etc., conditional on the framework being something else.

---

## 2.9 — REDUCE (deterministic clustering)

| Sub-step | Signal | Value |
|---|---|---|
| 2.9a Embed | clusters × dims | 42 × 2048d via `rotator:kd-embed` (4.1s) |
| 2.9b PCA | status | **skipped** (n_clusters=42 < 128 threshold) |
| 2.9b UMAP | dims | 2048d → 5d (n_neighbors=15, min_dist=0.0) (0.4s) |
| 2.9c k selection | candidates | k_meta=4 · k_volume=10 → k_target=7 → final_k=7 (clamp 4–12) |
| 2.9d KMeans sweep | k=6 | CH=17.47 sil=0.291 ← **picked** (CH winner) |
| 2.9d KMeans sweep | k=7 | CH=17.19 sil=**0.310** ← would win on silhouette |
| 2.9d KMeans sweep | k=8 | CH=17.04 sil=0.305 |
| 2.9d Cluster sizes | balanced | [7, 6, 5, 9, 8, 7] |
| 2.9e Thin merges | count | 0 (none below 15) |
| 2.9f Oversize splits | count | **1** — mid=3 had 157 files (over 20% cap = 121), split 79/78 |

**Diagnoses**:
- **CH vs silhouette mismatch**: k=6 wins on Calinski-Harabasz (global between-cluster separation) but k=7 wins on silhouette (local intra-cluster cohesion). The Ch02-class defect we see downstream is fundamentally a *low intra-cluster cohesion* problem — silhouette tracks the right thing.
- **The oversize split** at mid=3 took 157 files into 79+78. If the original mid=3 was already mixed-content, splitting it gives two mixed sub-buckets, propagating the defect. We have no diagnostic for "did the split improve coherence?".

**Improvements**:

1. **Swap k-selector to silhouette-primary, CH-tiebreaker** at `apps/fastapi/graphs/knowledge/reduce_cluster.py:407` — change `if ch > best_ch:` to `if (sil, ch) > (best_sil, best_ch):`. Currently CH wins → silhouette wins.

2. **Split-coherence diagnostic** — after each oversize-split, embed the slug content of each sub-bucket and compute centroid coherence; if BOTH sub-buckets have low coherence, flag in `record_reduce_splits.splits[i].sub_buckets[j].coherence` (currently None). Operator can spot bad splits.

3. **Re-run thin-merge after split** — if a split produces a sub-bucket below `_THIN_CHAPTER_FILE_THRESHOLD` (15), it should immediately be folded back into a neighbor. Currently thin-merge runs *before* splits, missing this case.

---

## 2.9g — Chapter coherence (the Ch02 detector)

Final 8 chapters with title-vs-files coherence:

```
RED  ch6  score=0.293  files=75   'Litellm Logging Guardrail Integrations and Related'
       lowest: cos=0.208  control-fallback-prompts-client-side
       lowest: cos=0.223  general-proxy-improvements
       lowest: cos=0.225  new-providers-models

YEL  ch1  score=0.364  files=86   'LiteLLM Essentials, Performance, Security'
       lowest: cos=0.263  contact-us
       lowest: cos=0.267  completion-function-completion

YEL  ch2  score=0.435  files=87   'Proxy Administration & Model Management'
       lowest: cos=0.323  complete-git-diff

YEL  ch3  score=0.457  files=39   'LiteLLM Proxy & UI Enhancements'
       lowest: cos=0.366  security-...-release-

YEL  ch4  score=0.357  files=44   'Batch API Management & Controls'
       lowest: cos=0.241  complete-git-diff
       lowest: cos=0.260  llm-translation-...-release-

YEL  ch5  score=0.459  files=78   'LLM Release Updates & Session Security'
       lowest: cos=0.366  demo-instance-...-release-

YEL  ch7  score=0.357  files=79   'LiteLLM Platform Updates & Migration'
       lowest: cos=0.282-0.286  general-proxy-improvements (×3)
```

**Cross-chapter pattern**: the same slug families dominate every chapter's worst-coherence list:
- `complete-git-diff` (in ch2, ch4, ch5, ch7, more)
- `general-proxy-improvements` (in ch4, ch6, ch7)
- `release-notes` siblings
- `contact-us`
- `new-providers-models`

These are **broad-scope filler** that pass the off-topic filter (they contain LiteLLM-relevant tokens) but don't fit any specific chapter. Mis-routed by definition.

**Improvements**:

1. **Chapter-level off-topic re-filter** (highest impact) — after meta-labeling in `reduce_cluster.py:embed_and_cluster_reduce`, for each chapter compute per-file cos against the chapter title embedding; drop files with cos < 0.30 to `unused_files`. **This auto-removes mis-routing without changing upstream filters.** Ch1's `contact-us` (cos 0.263) and Ch6's `control-fallback-prompts` (cos 0.208) would both be auto-dropped.

2. **Iterative title refinement** — if `coherence_score < 0.35`, re-run `META_LABEL_PROMPT` with the lowest-coherence files explicitly excluded from the meta-cluster input; re-name based on the high-coherence subset. Could resolve Ch6's "Logging Guardrail Integrations and Related" → either a more specific name, or a confirmation that the cluster is genuinely scattered (then dissolve and rebuild).

3. **Tighten thresholds** — current 0.35 red / 0.50 yellow. p10 of off-topic cosines was 0.271; chapter coherence below 0.40 is arguably already bad. Move red to 0.40, yellow to 0.55 after fix 1 lands.

4. **Block synthesize_chapter on RED** — currently we log a WARNING; the next stage runs anyway. Gate it: refuse to synthesize a chapter with coherence < 0.35 unless an operator overrides. Saves 10–30 min of wasted LLM time on a chapter that's structurally broken.

---

## 2.11 + 2.12 — Validation & coverage repair

Clean: 0 orphans, 0 hallucinated, 0 duplicate-assignments, valid=True. No improvement needed at this stage.

---

## Top 5 fixes ranked by impact

| # | Fix | Code site | Effort | Expected gain | Risk |
|---|---|---|---:|---|---|
| 1 | **Chapter-level off-topic re-filter** after REDUCE | `reduce_cluster.py:embed_and_cluster_reduce` after meta-labeling | 1 h | Auto-removes mis-routed slugs; flips RED→YEL and YEL→GREEN | low — additive only |
| 2 | **Off-topic threshold 0.30 → 0.27 + restore-via-kept-centroid** | `helpers.py:_OFF_TOPIC_THRESHOLD=1000` + new restore loop | 1 h | Recovers ~140 false-positive drops including real LiteLLM teaching content | low |
| 3 | **Silhouette as primary k-selector (CH tiebreaker)** | `reduce_cluster.py:407` 1-line change | 5 min + canary | Picks tighter local clusters; raises mean chapter coherence ~5–10% | medium — canary on Terragrunt too |
| 4 | **Structural deny-list filter** for `release-notes`, `git-diff`, `contact-us`, `b-https-` fragments | `helpers.py` off-topic stage | 30 min | Drops slug families that dominate every chapter's worst-coherence list | low |
| 5 | **Monolith-split sha256 dedup** | `post_ingest.py:split_monolith_if_needed` | 30 min | Eliminates 5+ perfect-dup ordinal sets; cleaner downstream embed + clustering | nil |

**Sequencing recommendation**: 1 → 4 → 2 → 3 → 5.

- Fix 1 alone resolves Ch6's RED by dropping the worst-coherence files into unused_files.
- Fix 4 removes the slug families that pollute every chapter — synergistic with 1.
- Fix 2 expands the keep set (more files survive); should be measured AFTER 1+4 so we can see if the expanded set has good or bad coherence.
- Fix 3 is the k-selection swap — needs its own canary because it changes cluster boundaries globally.
- Fix 5 is upstream hygiene — invisible in chapter coherence but cleans up cluster centroids.

---

## Iteration loop

Per `feedback_kd_quality_over_speed.md`: quality wins; runtime ~3 min per LiteLLM cycle is acceptable as the inner test loop.

1. Pick one fix from the ranked list.
2. Implement.
3. Skaffold redeploy.
4. Bust both caches (`_cache/planning/litellm/latest/_state.json` + `_cache/ingestion/litellm/latest/_state.json`).
5. Kick off run with fresh user_id.
6. Watch chapter_coherence record land on the page.
7. Compare RED/YEL/GREEN counts to this baseline + previous fix's result.

**Baseline (v3, this doc):** 1 RED, 6 YEL, 0 GREEN. Mean coherence ≈ 0.388.

---

## Cross-references

- `docs/KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md` — sub-step enumeration this analysis maps onto
- `docs/KD-DOCKER-QUALITY-FINDINGS-2026-05-15.md` — the chapter-synth defects that motivate this observability work
- `apps/fastapi/graphs/knowledge/distiller.py:175-700` — the planner orchestrator
- `apps/fastapi/graphs/knowledge/helpers.py:_filter_off_topic_files` (line 1012), `_dedup_chapter_files` (line 1129)
- `apps/fastapi/graphs/knowledge/reduce_cluster.py:embed_and_cluster_reduce` (line 247)
- `apps/fastapi/services/knowledge/post_ingest.py:split_monolith_if_needed` (line 148)
- `apps/fastapi/services/knowledge/planner_progress.py` — observability writer
- `apps/fastapi/routers/v1/knowledge/distiller.py:get_planner_observability` — read endpoint
- `apps/fasthtml/components/kd_observability.py:PlannerObservabilityFragment` — page rendering
- LiteLLM v3 snapshot raw JSON: `/tmp/v3-snapshot.json` (in current session's working tree)

---

## Per-chapter v3 snapshot (verbatim)

```
ch1  coherence=0.364 files= 86 title='LiteLLM Essentials, Performance, Security'
ch2  coherence=0.435 files= 87 title='Proxy Administration & Model Management'
ch3  coherence=0.457 files= 39 title='LiteLLM Proxy & UI Enhancements'
ch4  coherence=0.357 files= 44 title='Batch API Management & Controls'
ch5  coherence=0.459 files= 78 title='LLM Release Updates & Session Security'
ch6  coherence=0.293 files= 75 title='Litellm Logging Guardrail Integrations and Related'  ← RED
ch7  coherence=0.357 files= 79 title='LiteLLM Platform Updates & Migration'
ch8  coherence=...   files=... (omitted — re-run for full set)
```

Mean coherence ≈ 0.388. Worst-coherence files per chapter listed above under §2.9g.
