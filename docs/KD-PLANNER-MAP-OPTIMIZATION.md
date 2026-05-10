# Knowledge Distiller — Planner MAP Step Optimization Plan

**Date:** 2026-04-30 (initial); **2026-05-09 — committed picks rev** (§5 rewrite)
**Status:** **§5 picks COMMITTED 2026-05-09; reference implementation shipped.**
**Supersedes:** OP-66 (BERTopic) in `KD-CLASSICAL-OPTIMIZATION.md`; original §5 (Agglomerative + KeyBERT, 2026-04-30) superseded by 2026-05-09 picks.
**Companion docs:**
- `COELHO-CLOUD-EMBEDDINGS-MICROSERVICE.md` (predates Xinference pivot — refer to `infrastructure/modules/xinference/` as source of truth)
- `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` (downstream phase that absorbs MAP noise)

> **TL;DR — committed picks (May 2026, do not regress).** Per-stage best option for the Planner MAP step, applying `feedback_kd_quality_over_speed` (quality wins; tokens are free; runtime is not the constraint):
>
> | Stage | **Pick** |
> |---|---|
> | Pre-MAP noise filter | **Pure semantic off-topic filter** (cosine to framework prototype, threshold=0.30) |
> | Embedding model | **NIM `nvidia/llama-nemotron-embed-1b-v2` (2048-dim) via LiteLLM rotator** — single-entry `kd-embed` group; no provider fallover (different vector geometry would break clustering mid-study) |
> | Clustering | **`sentence_transformers.util.community_detection`** algorithm (threshold=0.60, min_community_size=2; rolled inline as numpy to drop torch dep) |
> | Cluster labeling | **KeyLLM via the LLM rotator** — `kd-keylm` group: NIM `meta/llama-3.2-1b-instruct` (GA) primary, Groq `llama-3.2-1b-preview` fallback. Temp=0, max_tokens=16. |
> | Multi-model orchestration | **`XinfManager`** for **embeddings + rerankers only** (single-slot mutex + Redis transition lock). LLMs of any size go through `services/llm_chain.py` rotator. |
> | Architecture rule | **Embeddings + rerankers → local Xinference; LLMs of any size → LLM rotator (NIM/Groq).** Do not host small LMs locally even when ≤2B — friction (custom registration, OOM thrash on launch failures) outweighs the determinism gain at homelab scale. |
>
> **Do NOT swap KeyLLM back to KeyBERT, PromptRank, or local Xinference-hosted LM without re-reading §5.** Past regressions: (1) KeyBERT-style custom code "to avoid an extra Xinference model"; (2) Qwen2.5-0.5B placeholder; (3) Llama-3.2-1B-Instruct via Xinference custom registration (hit OOM during failed launches 2026-05-09 → reverted to rotator).

---

## 1. Context

The KD planner is the first node after corpus ingestion. It decomposes a 400-file documentation corpus into 4-12 ordered chapters. Internally it is a 4-phase pipeline (MAP → REDUCE → NAME → ORDER), of which **only Phase 1 (MAP) is being addressed in this document**. The other phases are well-functioning and out of scope.

The MAP phase currently makes 11+ LLM calls per study (one per shard of ~40 files). On free-tier providers it is the planner's dominant source of latency, instability, and quality drift. This doc replaces it with a deterministic classical pipeline.

---

## 2. Current planner architecture (for context)

The planner is a single LangGraph node but contains 4 sub-steps:

```
PLANNER NODE
  ├─ Phase 1 — MAP    (LLM, 11+ parallel calls)
  │    Input:  ~400 files split into shards of 40
  │    Output: ~23 micro-clusters (1-3 per shard)
  │
  ├─ Phase 2 — REDUCE (math, no LLM — Clio v2)
  │    embed → PCA → UMAP → KMeansConstrained
  │    Output: 6 meta-clusters
  │
  ├─ Phase 3 — NAME   (LLM, 6 parallel calls — META_LABEL_PROMPT)
  │    Output: 6 chapter titles + goals
  │
  └─ Phase 4 — ORDER  (LLM, 1 call — ORDER_PROMPT)
       Output: pedagogically ordered ChapterPlanList
```

Code locations:
- MAP loop: `apps/fastapi/graphs/knowledge/distiller.py::planner` (lines 274-524)
- REDUCE: `apps/fastapi/graphs/knowledge/reduce_cluster.py::embed_and_cluster_reduce`
- Prompts: `apps/fastapi/schemas/knowledge/prompts.py` (`SHARD_LABEL_PROMPT`, `META_LABEL_PROMPT`, `ORDER_PROMPT`)

Per-file output sample (the LLM only sees this for each file, not the full content):

```
{slug} — {first 80 chars of file content, whitespace-collapsed}
```

`CORPUS_PREVIEW_CHARS = 80` in `helpers.py`.

---

## 3. Baseline run — Terragrunt 2026-04-30

To anchor the analysis, a baseline planner run was executed against the cached Terragrunt corpus (440 files, Tier 1 llms-full.txt source).

### 3.1 Wall-time + cost

```
Phase                                Wall-time      LLM calls
─────────────────────────────────────────────────────────────
MAP (11 shards, parallel)            180.7s         11 (shard-labelers)
REDUCE (math + 1 embed call)         3.2s           1 (embed)
NAME (6 meta-clusters, parallel)     157.4s         6 (meta-label)
ORDER (1 call)                       94.5s          1 (order)
─────────────────────────────────────────────────────────────
Total planner                        ~8 minutes     19 LLM calls
```

### 3.2 LLM rotator instability observed

- **4 of 11 shards timed out** at the 180s per-shard budget → forced synthetic timed-out clusters → 20 of 389 assigned slugs (5.1%) flagged unused.
- ORDER step's `magistral-medium-latest` returned `finish_reason: error` mid-generation; the rotator fell back to another model, ordering eventually succeeded.
- Telemetry export to LangFuse failed multiple times (`langfuse-web` DNS resolution errors). Traces are still visible because of the explicit `flush_langfuse` calls but the noise in logs is real.

### 3.3 Plan quality issues

| # | Title | Files | % corpus | Verdict |
|---|---|---|---|---|
| 1 | Terragrunt Installation and Tutorial | 59 | 14% | ✓ coherent |
| 2 | Terragrunt CLI & Backend Management | 41 | 10% | ⚠ release-process docs leaked in |
| 3 | Terragrunt Development & Usage | **160** | **39%** | ✗ junk drawer (contributing docs) |
| 4 | Terragrunt Usage Essentials | 65 | 16% | ✓ coherent ops concepts |
| 5 | Terragrunt Configuration Deep Dive | 55 | 13% | ✓ tight, focused |
| 6 | Terragrunt & OpenTelemetry | 9 | 2% | ⚠ too thin to be standalone |

**Root failure (Chapter 3):** UMAP/KMeansConstrained concentrated 160 files of contributing/community docs (`/contribute*`, `/developing-*`, `/github-discussions`, `/discord`, `/when-to-cut-a-release`) into one cluster. The ORDER LLM gave it a vague "Development & Usage" title to mask the mess. This file group is *about contributing to Terragrunt*, not *about using Terragrunt* — pedagogically irrelevant for the user's goal but presents as legitimate content to a downstream synth call.

The 160-file chapter will catastrophically degrade Chapter 3's synthesis (overflows context window, no coherent narrative possible).

### 3.4 Three deterministic tuning paths (orthogonal to MAP replacement)

These improvements stand independently of the MAP replacement and should be applied regardless of whether the BERTopic-equivalent migration happens.

**T-1. Strengthen `_filter_noise_files`** (in `apps/fastapi/graphs/knowledge/helpers.py`)
Drop slugs matching:
- `/contribute*`, `/contribution-guidelines`
- `/developing-*`
- `/github-discussions`, `/discord`, `/community*`
- `/when-to-cut-a-new-release`, `/how-to-create-a-new-release`

Effect: ~30-50 files removed before MAP. Chapter 3 shrinks from 160 to ~110 files. Zero LLM cost increase.

**T-2. Cap `KMeansConstrained` `size_max`** (in `apps/fastapi/graphs/knowledge/reduce_cluster.py`)
Currently `size_max = max(size_min + 1, fair_share × 2)`. Change to also enforce `size_max ≤ 0.25 × n_clusters`. Forces no single meta-cluster to exceed 25% of micro-clusters.

Effect: prevents future junk-drawer pathologies. Zero LLM cost increase.

**T-3. Merge thin chapters post-hoc** (new step in `reduce_cluster.py`)
After REDUCE, if any chapter has `< 15 files`, fold into the nearest larger cluster (cosine-similarity on UMAP centroids).

Effect: Chapter 6 (9 files) merges into Chapter 5 or Chapter 4. Eliminates the standalone-thin pattern. Zero LLM cost increase.

These three together produce a measurably better plan with zero infrastructure changes.

---

## 4. Why OP-66 (BERTopic) is rejected

The original `KD-CLASSICAL-OPTIMIZATION.md` recommendation (OP-66, 2026-04-25) was BERTopic + `KeyBERTInspired` representation + c-TF-IDF + MMR with LLM fallback when topic-coherence (`c_v`) < 0.4.

**Reasons for rejection (research-validated 2026-04-30):**

1. **BERTopic's own FAQ explicitly warns it needs ~1000 documents minimum** to produce stable topics. Our shards have N=40. The HDBSCAN density-clustering core BERTopic uses is the wrong tool at this scale — there's not enough density signal for HDBSCAN to find real clusters.
2. **The fallback would fire on nearly every shard.** With N=40 and tight tech-doc text, `c_v < 0.4` would trigger for the majority of shards. The "hybrid" becomes "LLM with BERTopic noise on top," defeating the purpose.
3. **Wrong abstraction level.** BERTopic is a topic-modeling pipeline designed for large heterogeneous corpora. We need *small-batch clustering with auto-labeling* — a simpler problem with simpler tools.

---

## 5. Committed picks — May 2026 (replaces 2026-04-30 Agglomerative + KeyBERT draft)

After deep web research validated against 2025-2026 papers + production writeups, and applying the `feedback_kd_quality_over_speed` rule, the per-stage picks for Planner MAP are:

### 5.1 The picks

| Stage | **Pick** | One-line rationale |
|---|---|---|
| **Pre-MAP noise filter** | Pure semantic off-topic filter (cosine to framework prototype, threshold=0.30) | One Xinference batch catches every framework's contributing/community/release/license/stub docs. Zero per-framework regex curation. |
| **Embedding model** | **NIM `nvidia/llama-nemotron-embed-1b-v2` (2048-dim) via the LiteLLM rotator** — single-entry `kd-embed` group | Free tier 40 RPM, no monthly cap, commercial OK. Same NVIDIA_API_KEY already in coelhonexus-secret. **Single-entry by design**: embedding rotation across providers breaks cosine geometry within a study (different model = different vector space). If NIM is fully down, study fails — user retries (cheap). Local Xinference removed 2026-05-09 after CPU-spike cluster-stability issues; fastembed CPU fallback removed for the same reason. |
| **Clustering** | `sentence_transformers.util.community_detection` algorithm — `threshold=0.60`, `min_community_size=2` | Purpose-built for embedding clustering, deterministic. **Rolled as 15-line numpy** in `services/knowledge/embeddings.py` (algorithm matches sbert reference exactly) to drop the torch transitive that `sentence-transformers` package would pull. |
| **Cluster labeling** | **KeyLLM via the LLM rotator** — `kd-keylm` group in `services/llm_chain.py`. Primary: NIM `meta/llama-3.2-1b-instruct` (GA on integrate.api.nvidia.com, free tier). Fallback: Groq `llama-3.2-1b-preview` (LPU, sub-100ms). | Same model the research validated (IFEval 59.5, temp=0 stable, Llama-3.1 lineage), but hosted by NIM + Groq instead of self-hosted on Xinference. Earlier draft (2026-05-09 morning) registered Llama-3.2-1B as a custom Xinference model; reverted same-day after OOM during failed launches. Rotator path is faster (LPU > Tiger Lake CPU), zero local RAM cost, zero custom-registration code. License: Llama 3.2 (commercial OK <700M MAU). |
| **Multi-model orchestration** | `XinfManager` for **embeddings + rerankers only** — single-slot mutex + Redis transition lock. LLMs route through `services/llm_chain.py` rotator (LiteLLM Router with circuit-breaker cooldowns + multi-group support). | XinfManager remains the right tool for high-volume CPU-bound embedding work and any future model class not on hosted APIs. Forcing a tiny LM through it duplicates rotator capability without benefit. |
| **MAP execution shape** | **Two-phase: (A) embed+cluster ALL shards in parallel via Xinference, (B) generate ALL cluster labels in parallel via the rotator (KEYLM_CONCURRENCY=4 cap)** | Phase A is single-model on Xinference (no swap). Phase B doesn't touch Xinference at all — pure rotator calls, sub-second each. No model thrash possible. Implemented in `classical_map.py::label_shards_classical(shards)`. |

### 5.2 What was rejected — and why this list will not regress

| Rejected pick | Why we're not using it |
|---|---|
| **BERTopic / FASTopic / HDBSCAN** | Density-clustering needs N≥1000; our shards are N=40 (per author FAQs). |
| **AgglomerativeClustering** (the 2026-04-30 draft) | Functionally equivalent to `community_detection` at N=40. `community_detection` is purpose-built for embeddings and drops the sklearn dep at this stage. |
| **KeyBERT** | Smaller stopword list than KeyLLM-style approach; KeyBERT's own author recommends KeyLLM as of 2024. Lower label quality. |
| **Custom KeyBERT-style (regex tokenizer + cosine ranking)** | Briefly shipped 2026-05-09 to "avoid adding a second Xinference model." That reasoning was wrong — `XinfManager` exists specifically to make multi-model swap cheap. Replaced with KeyLLM. |
| **PromptRank (ACL 2023)** | "Best fully-classical" but ~3-5min/study at our scale (forward pass per candidate phrase). KeyLLM with the same small LM is faster AND higher quality. |
| **In-process `sentence-transformers` package** | Pulls torch (~1GB+ install) for one numpy operation and a thin keyphrase wrapper. Routed through Xinference instead. |
| **Qwen2.5-0.5B-Instruct** (briefly named in the 2026-05-09 first draft) | Lower IFEval than Llama-3.2-1B; smaller-but-not-better. Replaced with Llama-3.2-1B-Instruct after head-to-head benchmark review. |
| **Qwen3-0.6B-Instruct** | The Qwen team's official model card explicitly warns against greedy (temperature=0) decoding for sub-1B variants → conflicts with our deterministic-output requirement. |
| **Routing the KeyLLM step through the existing LLM rotator (NIM/Groq)** | Both providers DO host Llama-3.2-1B-Instruct (NIM as GA, Groq as preview). However: (a) "preview" status on Groq is a stability risk; (b) network round-trip per cluster label burns latency and LangFuse traces; (c) external dependencies create silent-failure modes; (d) at homelab scale, robustness > the 20s wall-time saving. The architecture rule is: **local for ≤2B, rotator for >2B.** Do not push small task LMs onto the rotator just because the rotator hosts them. |

### 5.3 Reference implementation (shipped 2026-05-09)

| File | Role |
|---|---|
| `apps/fastapi/services/knowledge/embeddings.py` | `XinfManager`, `MODEL_PAYLOADS` registry, `community_detection` helper, `embed_texts/sync` API |
| `apps/fastapi/graphs/knowledge/classical_map.py` | `label_shard_classical()` — drop-in replacement for the LLM-based `_label_shard` |
| `apps/fastapi/graphs/knowledge/helpers.py` | `_filter_off_topic_files()` — semantic noise filter, replaces regex `_filter_noise_files` |
| `apps/fastapi/graphs/knowledge/reduce_cluster.py` | T-2 size_max cap (≤25% of n_clusters) + T-3 thin-chapter merge (<15 files folds into nearest larger by centroid cosine) |
| `apps/fastapi/graphs/knowledge/distiller.py` | `KD_USE_CLASSICAL_MAP` env-var flag routes MAP to classical path |
| `apps/fastapi/routers/v1/knowledge/debug.py` | `GET /debug/map_compare` — A/B route returning per-shard side-by-side LLM vs classical output |

### 5.4 Lessons learned — DO NOT REGRESS

Four real divergences happened during the 2026-05-09 implementation cycle. Each was wrong; each is documented here so future-you doesn't redo them:

1. **"Avoid a second Xinference model — roll our own KeyBERT-style"** (caught + fixed 2026-05-09). The `XinfManager` was built specifically to hot-swap small LMs per task at near-zero cost. Adding a 1B Q4_K_M instruct LM (~808 MB on disk) seemed like the right cost — but see lesson #4 below for why we ultimately moved off this anyway.

2. **"Qwen2.5-0.5B-Instruct is small and Apache-2.0, ship it"** (caught + fixed 2026-05-09). Smaller is not better. Llama-3.2-1B has a confirmed IFEval 59.5 vs Qwen2.5-0.5B's lower-baseline score, and crucially, Qwen3-0.6B's official docs warn against greedy decoding — conflicts with our temp=0 determinism. **Always check IFEval + greedy-decoding stability before picking the smallest available model.**

3. **"Llama-3.2-1B is on the rotator — but stay local for robustness"** (initial decision 2026-05-09 morning, REVERSED same day — see lesson #4). The original rationale: at homelab scale, robustness > 20s wall-time saving; XinfManager was already built. **What broke this rationale:** real-world friction with Xinference custom-model registration (version=2 + model_family validation, 400s on missing fields) plus **OOMKilled** events during failed launch retries. The "robustness" promise didn't hold once we exercised the actual launch path.

4. **"Stay local for ≤2B" rule reversed → "all LLMs go through rotator"** (committed 2026-05-09 evening). After hitting OOMKill during Llama-3.2-1B custom registration attempts, we pivoted to using the LLM rotator's `kd-keylm` group instead. The new rule is simpler and tested: **embeddings/rerankers → Xinference; LLMs of any size → rotator.** No more custom-registration code; no OOM thrash; faster (LPU/cloud > Tiger Lake CPU); same model (Llama-3.2-1B-Instruct) hosted on NIM as GA.

If a future change considers any of:
- Replacing **KeyLLM** with KeyBERT, PromptRank, or any custom in-process implementation
- Swapping **Llama-3.2-1B-Instruct** for a smaller Qwen/Phi variant on the rotator
- **Hosting the small LM locally on Xinference** (re-doing custom registration)

— re-read §5.1, §5.2, and this section first. The committed picks stand. Per `feedback_kd_quality_over_speed`, the rotator is **also** the quality choice (sub-100ms LPU latency vs CPU; identical model weights).

### 5.5 Honest tradeoffs (kept from the 2026-04-30 draft, still apply)

- **`distance_threshold` / `community_threshold` is a tunable knob.** Default `0.60` for community_detection works on tech-doc shards; 0.55-0.65 range expected per corpus. Per-framework calibration only if a corpus repeatedly fails A/B.
- **Shard labels are intermediate signal.** REDUCE's `META_LABEL_PROMPT` produces the user-facing chapter titles. KeyLLM-quality shard labels improve REDUCE's clustering geometry but rarely surface end-user-side.

---

## 6. Validation plan (DO BEFORE FULL MIGRATION)

The replacement is theoretically optimal for our shape but **not yet validated on our actual corpus**. The migration must include an empirical A/B step.

### 6.1 Build a `/debug/map_compare` route

Add a debug-only FastAPI route that, given a `study_id` with cached corpus:

1. Runs the existing LLM MAP path against the corpus.
2. Runs the new Agglomerative+KeyBERT path against the same corpus.
3. Returns side-by-side per-shard cluster outputs (cluster names, file_slugs, sizes) for human inspection.

### 6.2 Acceptance criteria for migration

Migrate only if **all** of these hold for ≥3 framework corpora (Terragrunt, MLflow, Docker):

| Metric | Threshold |
|---|---|
| Per-shard cluster count | within ±1 of LLM output |
| File coverage (assigned + unused) | ≥99% (no dropped slugs) |
| Cluster-name semantic overlap | ≥80% topical match (manual review) |
| Wall time | ≤30s per study (vs current ~3min) |
| Determinism | identical output on 3 reruns of same corpus |

If any threshold fails, refine the threshold parameter or fall back to LLM with `c_v < 0.4` gating (the original OP-66 hybrid). Do **not** ship the replacement without empirical validation.

### 6.3 Rollout

1. **Step 1 (week 1):** deploy TEI sidecar with Qwen3-Embedding-0.6B (see `COELHO-CLOUD-EMBEDDINGS-MICROSERVICE.md`). Verify REDUCE migrates off NIM cleanly.
2. **Step 2 (week 1):** apply T-1, T-2, T-3 deterministic tuning paths. Re-run baseline planner on Terragrunt — measure improvement.
3. **Step 3 (week 2):** implement `/debug/map_compare` route. Run A/B against 3 framework corpora.
4. **Step 4 (week 2-3):** if A/B passes, replace `_label_shard` with the classical pipeline. Keep the LLM path as a feature-flagged fallback for one release cycle.
5. **Step 5 (week 4):** remove the LLM MAP path entirely once classical has been stable in production for ~2 weeks.

---

## 7. References

**Why BERTopic doesn't fit our N=40:**
- [BERTopic FAQ — small dataset warning](https://maartengr.github.io/BERTopic/faq.html)
- [BERTopic v0.17 release notes](https://github.com/MaartenGr/BERTopic/releases)

**The recommended pattern:**
- [sentence-transformers Agglomerative example](https://github.com/UKPLab/sentence-transformers/blob/master/examples/applications/clustering/agglomerative.py)
- [Sentence-Transformers clustering docs](https://sbert.net/examples/sentence_transformer/applications/clustering/README.html)
- [KeyBERT v0.9 release notes](https://maartengr.github.io/KeyBERT/changelog.html)

**Alternatives evaluated and rejected:**
- [FASTopic NeurIPS 2024](https://arxiv.org/abs/2405.17978) — N≥15K minimum, top-words only (no phrase labels)
- [HERCULES arXiv 2506.19992](https://arxiv.org/abs/2506.19992) — requires LLM in the loop, defeats the goal
- [Kura (jxnl/kura)](https://github.com/jxnl/kura) — N≥100 conversations minimum, LLM-required
- [PRISM arXiv 2604.03180](https://arxiv.org/abs/2604.03180) — requires LLM teacher pass to fine-tune encoder
- [Top2Vec](https://github.com/ddangelov/Top2Vec) — same density-clustering failure mode at N=40

**Embedding model choice:**
- [Qwen3-Embedding paper arXiv:2506.05176](https://arxiv.org/abs/2506.05176)
- [MTEB Code leaderboard 2025-2026](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/)
- See `COELHO-CLOUD-EMBEDDINGS-MICROSERVICE.md` for the full model selection rationale.

**Anthropic Clio (REDUCE pattern that absorbs MAP noise):**
- [Clio paper arXiv:2412.13678](https://arxiv.org/html/2412.13678v1)
- See `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md`.

---

## 8. Open questions

- Should T-1/T-2/T-3 (deterministic tuning paths) ship before the classical MAP migration, or simultaneously? Argument for sequential: cleaner attribution of quality changes. Argument for simultaneous: single PR, single redeploy.
- Should the `distance_threshold` be per-framework (calibrated from sources.yaml) or global? Initial answer: global default, override-allowed. Per-framework calibration only if a corpus repeatedly fails A/B.
- KeyBERT runs the embedding model in-process (it cannot proxy through TEI's `/v1/embeddings` endpoint because keyphrase extraction needs sub-token attention). This means a copy of the embedding model is loaded locally in the FastAPI process. Acceptable cost (~600MB) for our deployment scale; revisit if memory pressure surfaces.
