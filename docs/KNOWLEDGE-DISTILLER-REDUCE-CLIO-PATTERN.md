# Knowledge Distiller — REDUCE Step: Clio Pattern

Dated: 2026-04-22

## Problem

Single-shot REDUCE (`CHAPTER_REDUCE_PROMPT` via
`llm.with_structured_output(ChapterPlanList, method="function_calling")`) fails
reliably on large corpora. Observed on 300 micro-clusters from a 4087-file
LangChain/LangGraph/DeepAgents study:

| Provider | Error | Root cause |
|---|---|---|
| NVIDIA NIM | HTTP 504 Gateway Timeout after 300s | Free-tier gateway cuts any single call at 300s; reasoning models (GLM-5.1, Qwen3.5-397B, DeepSeek-v3.2, Kimi K2.5) burn the budget on `<think>` blocks at 25K+ input tokens and never return the tool-call |
| Groq | HTTP 413 Payload Too Large | Free-tier TPM cap is per-model, NOT a context-window issue: `llama-3.3-70b-versatile` 12K TPM, `gpt-oss-120b` 8K TPM, `llama-3.1-8b-instant` 6K TPM. Only `meta-llama/llama-4-scout-17b-16e-instruct` has 30K TPM — still marginal for 25-35K prompts |

Full fallback-chain cascade exhausts all 14 models. Worst case 42+ min, then a
chained-failure sentinel stops the pipeline.

## Root cause reframed

Three independent problems stacked:

1. **Groq 413 = TPM rate limit, not context window.** Every chain model has
   128K+ context. 413 is just the free-tier per-minute token budget.
2. **NIM 504 = gateway timeout, not our timeout.** We set 420s per-model;
   NIM cuts at ~300s server-side. Streaming bypasses this for well-tuned
   models but isn't a full fix.
3. **25K+ token structured-output calls are architecturally brittle.** Even
   when they succeed, they hit "lost in the middle" (items at position 150/300
   get ignored), produce truncated `finish_reason=length`, and are a
   single-point-of-failure.

## Solution: Anthropic Clio hierarchizer (arxiv 2412.13678)

Decouple grouping (deterministic, zero LLM tokens) from naming (tiny LLM
calls, parallel). No single LLM call ever sees all 300 clusters.

```
MAP (unchanged): N shard-labelers emit ~300 micro-clusters
REDUCE (new):
  1. Embed each (cluster_name + description) locally via fastembed
     BAAI/bge-small-en-v1.5 (384-dim, ONNX, no torch)
  2. k-means with silhouette sweep over k∈[4, min(12, N/3)]
     → M meta-clusters deterministically
  3. asyncio.gather M parallel META_LABEL_PROMPT calls, each sees
     ~30 micro-clusters (~3K tokens) and emits (title, goal)
  4. One ORDER_PROMPT call (~2K tokens) sequences the M chapters
  5. assigned_files = union of member micro-clusters' file_slugs
     (deterministic, no LLM needed)
  6. Existing coverage-repair handles orphans + hallucinated slugs
```

Biggest LLM call is ~3K tokens — safely under every free-tier constraint.

## Files

| File | Role |
|---|---|
| `apps/fastapi/services/knowledge/embeddings.py` | Local fastembed wrapper; async via `asyncio.to_thread`; module-level singleton (one-time ~5s cold start) |
| `apps/fastapi/graphs/knowledge/reduce_cluster.py` | `embed_and_cluster_reduce()` — the whole REDUCE |
| `apps/fastapi/schemas/knowledge/prompts.py` | `META_LABEL_PROMPT` (label one meta-cluster) + `ORDER_PROMPT` (order M chapters). Old `CHAPTER_REDUCE_PROMPT` kept in module, just unreferenced |
| `apps/fastapi/schemas/knowledge/agents.py` | `MetaLabelDraft` (title+goal only) and `OrderedIndices` (permutation + rationale) |
| `apps/fastapi/graphs/knowledge/distiller.py` | `planner()` REDUCE block replaced with a call to `embed_and_cluster_reduce`. Validation + coverage-repair run unchanged downstream |
| `apps/fastapi/pyproject.toml` | Added `scikit-learn` (fastembed was already present) |

## Measured results (2026-04-22, 305 micro-clusters from DeepAgents + LangChain + LangGraph)

| Step | Wall-clock | Notes |
|---|---|---|
| Embed 305 × 384-dim | 58.66s | fastembed ONNX on CPU; cold-batch overhead dominates; subsequent runs faster |
| k-means sweep over k∈[4,12] + silhouette | 5.39s | picked k=4, sizes=[110, 62, 59, 74] |
| M=4 parallel label calls | 32.18s | asyncio.gather; fits every model's context |
| Ordering call | 37.77s | single ~2K-token call |
| **Total REDUCE** | **~2 min** | vs. **>42 min of failure** on previous run |

## Known quality issues + tuning knobs

### 1. Low silhouette score → k defaults to minimum

Silhouette on same-framework micro-cluster embeddings tends to be low
(~0.06 on the test run) because all descriptions are semantically close in
embedding space. The sweep then picks k = `_MIN_CHAPTERS` = 4 because higher
k has even lower silhouette.

**Symptom:** 4 chapters for a 3800-file corpus → synthesizer gets 300-1300
files per chapter → synthesis prompt blows past limits.

**Fix knobs (in `reduce_cluster.py`):**

- **Raise `_MIN_CHAPTERS` (current: 4).** For large corpora (>200
  micro-clusters) set to 8. Schema cap is 12.
- **Add UMAP pre-reduction.** Install `umap-learn`, apply
  `UMAP(n_components=10).fit_transform(vectors)` before k-means. BERTopic
  and Clio both do this; dramatically improves k-means/HDBSCAN quality on
  text.
- **Switch to HDBSCAN with `min_cluster_size=15, min_samples=5`.** Density-
  based; handles same-framework overlap better; naturally emits "noise"
  points for outliers.
- **Prefer higher k on ties.** Add small bias to silhouette: `score -
  0.01 * (12 - k)` so ties break toward more chapters.

### 2. Double-assigned slugs across chapters

~20 slugs in the test run appeared in 2 chapters. Root cause: MAP's
shard labeler puts the same slug in 2 overlapping micro-clusters within a
shard. When k-means routes those to different meta-clusters, the slug
ends up in 2 chapters. The coverage-repair pass (distiller.py lines 547-579)
handles orphans + hallucinated but NOT double-assignment.

**Fix (5-line addition to `reduce_cluster.py`):** after `_label_one` calls
complete, build `slug → meta_id_list`, then for each duplicated slug keep
it only in the meta-cluster whose centroid is closest to that slug's
embedding. Or simpler: drop from all but the smallest meta_id.

### 3. Per-meta-cluster labels can repeat generic titles

If two meta-clusters emerged from similar micro-cluster distributions, the
LLM can emit "State Management" for one and "State" for the other. The
ordering pass doesn't catch this because it only picks indices, not titles.

**Fix:** add a de-dup pass after `asyncio.gather` — if two drafts have
Jaccard(title_tokens) > 0.5, merge them (union `assigned_files`, pick
longer title).

## Downstream consequence: synthesizer also needs attention

With 4 chapters, synth prompts balloon (one chapter had ~1300 files). Even
with the existing `CHAPTER_FILES_MAX_CHARS = 180_000` (≈45K tokens) cap,
NIM reasoning models 504 and Groq 413s persist at synthesis. The
fallback chain still converges on a working model (NIM Nemotron-3-super is
non-reasoning, 1M context, fast), but each chapter cascades ~10 min.

**Mitigations in order of invasiveness:**

1. **Raise `_MIN_CHAPTERS` to 8+** (Clio-level fix) — chapters stay
   digestible, synth prompts shrink.
2. **Lower `CHAPTER_FILES_MAX_CHARS` to 80_000** (~20K tokens) — fits every
   model's TPM, quality cost is truncated chapter corpus.
3. **Sub-chapter synthesis batching** — split a big chapter's files into
   groups of 20, synthesize each group, merge. Bigger refactor.

## Dependencies

- `fastembed` (already present; ONNX runtime, no torch)
- `scikit-learn` (added 2026-04-22)
- `numpy` (transitively present)

No external embedding API, no GPU, no MLflow (Clio clustering is stateless
per-run).

## Next steps (priority-ordered, 2026-04-22)

**1. Raise `_MIN_CHAPTERS` from 4 to 8** (`graphs/knowledge/reduce_cluster.py`)
   - Root cause of downstream synth cascade: k=4 → chapters hold 300-1300
     files each → synth prompt ≈ `CHAPTER_FILES_MAX_CHARS`=180K chars ≈ 45K
     tokens → NIM reasoning models 504, Groq llama-4-scout 413.
   - With k=8 each chapter holds ~50-150 files → synth prompt ~15-20K tokens
     → fits every model's TPM.
   - 1-line change.

**2. Cross-meta-cluster slug dedup** (`graphs/knowledge/reduce_cluster.py`)
   - After `asyncio.gather(_label_one, ...)`, build `slug → [meta_ids]`, drop
     the slug from every meta-cluster except the one whose centroid is
     closest to that slug's own embedding (or simpler: keep in smallest
     meta_id). Prevents the ~20 double-assignments observed on 305-cluster
     run.

**3. Commit + retest** the working Clio REDUCE + #1 + #2 fixes together.
   MAP→REDUCE was proven 2 min vs. >42 min previous failure; bank that win
   before further tuning.

**4. Deeper REDUCE quality knobs** (only after #1-#3 proven end-to-end)
   - Add UMAP pre-reduction (384-dim → 10-dim) before k-means — BERTopic /
     Clio both report this materially improves clustering on text.
   - Or switch to HDBSCAN for density-based clustering with noise detection.
   - Prefer-higher-k tie bias in silhouette selection.
   - Jaccard-title dedup on `_label_one` output to catch near-duplicate
     chapter titles like "State Management" + "State".

**5. Synth-level mitigations** (if #1 doesn't fully fix synth timeouts)
   - Lower `CHAPTER_FILES_MAX_CHARS` from 180_000 to 80_000 (~20K tokens).
   - Sub-chapter batching: split a chapter's files into groups of 20,
     synthesize each group, merge. Bigger refactor; defer unless needed.

## v2 recipe (2026-04-22, research-tuned)

After the v1 ship, deep research into production LLM pipelines (Clio, Kura,
BERTopic, HERCULES) revealed why v1's silhouette sweep was picking `k=4`
every time on same-domain data. Silhouette ~0.06 on 305 tight clusters isn't
a tuning failure — it's a structural property of same-domain embeddings. The
principled fix is to stop using geometry to pick k on same-domain corpora,
pick from corpus size + pedagogical target instead, then let clustering
serve that target. This is literally what Clio does (Appendix G.7).

### v2 changes (all shipped)

**1. Embedding model upgrade: `bge-small-en-v1.5` → `bge-base-en-v1.5`**
- 384-dim → 768-dim. MTEB Clustering: 43.82 → 45.77 (+2 points).
- ~220 MB download (vs ~66 MB), ~2× CPU inference; still <5 s for 300 items.
- Fastembed-compatible ONNX, no torch.

**2. UMAP pre-reduction before clustering**
```python
UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric='cosine', random_state=42)
```
- BERTopic canonical defaults. Un-collapses local neighborhoods that get
  flattened on the L2-normalized hypersphere.
- Expected silhouette delta: 0.06 → ~0.25 on same-domain text.
- ~1-3 s extra on CPU for 300 items, deterministic via seed.
- Falls back to raw embeddings if UMAP fails for any reason.

**3. Size-based k_target (replaces silhouette sweep)**
```python
k_meta   = round(n_clusters / 40)   # Clio Appendix G.7 ratio
k_volume = round(n_files / 50)      # pedagogical volume target
k_target = round((k_meta + k_volume) / 2)
```
- On the 305-cluster test: k_meta=8, k_volume=~80→12, average=10. Clamped
  to max=12. vs v1's silhouette picking k=4.
- Zero new LLM cost (deterministic arithmetic).

**4. KMeansConstrained with balanced sizes**
```python
fair_share = n_clusters / k
size_min = max(1, fair_share / 3)     # prevent 3-file chapters
size_max = max(size_min + 1, fair_share * 2)  # prevent 1300-file chapters
KMeansConstrained(n_clusters=k, size_min=size_min, size_max=size_max, random_state=42)
```
- Formulated as min-cost flow (pure Python, MIT license, CPU-only).
- Directly solves the "3-file chapter next to 1300-file chapter" pathology.
- Falls back to plain `sklearn.cluster.KMeans` if the constraint is infeasible.

**5. Calinski-Harabasz tiebreaker within k_target ± 1**
- Sweeps `{k_target-1, k_target, k_target+1}`, picks max CH.
- CH is more reliable than silhouette for picking among adjacent k on
  tight clouds because it normalizes by `(n-k)/(k-1)`.
- Both CH and silhouette are logged for debuggability; CH wins in selection.

**6. Cross-meta-cluster slug dedup**
- Root cause: MAP's shard labeler sometimes puts the same slug in 2
  overlapping micro-clusters. k-means routes those to different meta-clusters.
- Resolution algorithm:
  1. Collect `slug → [list of meta_ids claiming it]`
  2. Majority vote on micro-cluster membership
  3. If tied: closest-centroid (on UMAP-reduced space where k-means ran)
  4. Remove slug from losing meta-clusters' `assigned_files`
- ~20 duplicates eliminated on the 305-item test. Runtime: sub-100ms.

### New dependencies

- `umap-learn` — pure Python, deterministic with `random_state`
- `k-means-constrained` — MCF-based balanced k-means (joshlk/k-means-constrained)

Both CPU-only, no GPU required.

### Observability

Every run now logs k selection breakdown, sweep results, cluster sizes, CH
AND silhouette scores, dedup count, and a rich `reasoning` field embedded
in the ChapterPlanList for post-hoc debugging.

Example log lines from a healthy run:

```
[reduce-cluster] 305 micro-clusters, 3792 assigned slugs, 388 shard-unused
[reduce-cluster] embedded 305×768d in 3.12s (fastembed BAAI/bge-base-en-v1.5)
[reduce-cluster] UMAP 768d → 5d in 1.47s (n_neighbors=15, min_dist=0.0, metric=cosine)
[reduce-cluster] k selection: k_meta=8 (n_clusters/40), k_volume=12 (n_files/50), k_target=10
[reduce-cluster] k=10 CH=72.4 silhouette=0.253 sizes=[28, 31, 30, 32, 29, 33, 28, 30, 34, 30] sweep=[k=9(CH=68.2,sil=0.241), k=10(CH=72.4,sil=0.253), k=11(CH=69.8,sil=0.247)] in 0.91s
[reduce-cluster] labeled 10 meta-clusters in 28.45s (parallel)
[reduce-cluster] slug dedup: resolved 18 double-assigned slugs in 0.07s
[reduce-cluster] ordered 10 chapters in 31.22s
```

### Expected combined improvement over v1

| Metric | v1 | v2 |
|---|---|---|
| Silhouette | 0.063 | ~0.25 (on UMAP-reduced space) |
| Chapter count | 4 (floor) | 8-12 (pedagogically right) |
| Chapter size spread | `[4, 61, 182, 58]` | balanced `[~30 each]` via constraint |
| Double-assigned slugs | ~20 per run | 0 (dedup'd) |
| Deterministic? | yes | yes (all steps seed-pinned) |
| Extra wall-clock | — | +3-5 s (UMAP + larger embeddings) |
| Downstream synth cascade | BAD (chapters too thick) | OK (each chapter ~50-150 files → ~15K-token synth prompt) |

### Deferred — only if v2 proves insufficient

- **Nomic-embed-text-v1.5 with `clustering:` task prefix** — the only model
  with a training objective specifically for clustering separation. Same cost
  as bge-base. Experimental toggle via `NVIDIA_EMBEDDING_MODEL` env var
  pattern once validated.
- **HDBSCAN** — explicitly rejected by Clio for same-domain corpora; dumps
  50%+ of points to noise without heavy patching. Keep as emergency option
  for future out-of-domain batches.
- **Agglomerative clustering with distance_threshold** — viable alternative
  to k-means; gives a free dendrogram for progressive drill-down. Similar
  quality to k-means on UMAP-reduced space.
- **Must-link / cannot-link constraints** — if we later want to force
  co-assignment of certain cluster names, pre-merge them deterministically
  before k-means rather than using COP-Kmeans (not production-ready).

## Research sources backing v2

- **Clio paper** (Anthropic, arXiv 2412.13678) — §G.5, §G.7, §C.1.3 — the
  authoritative "what Anthropic actually does in production" reference.
  Explicitly tried HDBSCAN + agglomerative, rejected both, kept k-means.
  Used `n_prev / 40` ratio for k selection.
- **Kura** (jxnl/kura, meta_cluster.py) — open-source Clio reproduction.
  `max_clusters=10` + `ceil(n/2)` default.
- **BERTopic** — Best Practices page: `UMAP(n_neighbors=15, n_components=5,
  min_dist=0.0, metric='cosine')` is the canonical production recipe.
- **HERCULES** (arXiv 2506.19992) — June 2025 hierarchical k-means + LLM
  paper. Confirms the map-reduce-cluster pattern is the template.
- **k-means-constrained** (joshlk on GitHub) — MCF-based balanced clustering.
  MIT licensed, pure Python.
- **Balanced K-Means Revisited** (cs.uef.fi/sipu) — theoretical basis for
  balanced clustering and why MSE is slightly worse but pedagogical value
  is higher.

## Related references

- Anthropic Clio: [Privacy-Preserving Insights into Real-World AI Use](https://arxiv.org/abs/2412.13678)
- LLMxMapReduce hierarchical collapse: [arxiv 2410.09342](https://arxiv.org/abs/2410.09342)
- HERCULES: [arxiv 2506.19992](https://arxiv.org/abs/2506.19992)
- Clio reproduction (Kura): https://github.com/jxnl/kura
- BERTopic Best Practices: https://maartengr.github.io/BERTopic/getting_started/best_practices/best_practices.html
- k-means-constrained: https://github.com/joshlk/k-means-constrained
- LangGraph collapse_summaries tutorial: https://docs.langchain.com/oss/python/langgraph/use-graph-api
- NIM bug report 366612 (14 cataloged structured-output quirks): https://forums.developer.nvidia.com/t/366612
- Groq free-tier rate limits: https://console.groq.com/docs/rate-limits
