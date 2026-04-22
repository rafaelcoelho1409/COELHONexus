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

## Related references

- Anthropic Clio: [Privacy-Preserving Insights into Real-World AI Use](https://arxiv.org/abs/2412.13678)
- LLMxMapReduce hierarchical collapse: [arxiv 2410.09342](https://arxiv.org/abs/2410.09342)
- LangGraph collapse_summaries tutorial: https://docs.langchain.com/oss/python/langgraph/use-graph-api
- NIM bug report 366612 (14 cataloged structured-output quirks): https://forums.developer.nvidia.com/t/366612
- Groq free-tier rate limits: https://console.groq.com/docs/rate-limits
