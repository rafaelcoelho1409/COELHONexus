# Planner FastMCP validation + FGTS-VA empirical results (2026-05-23)

First end-to-end Planner run on FastMCP with the **FGTS-VA bandit actually firing** (previous runs were silently falling back to LiteLLM router-shuffle due to a Redis cell dim-drift bug). Validates the bandit-core upgrade shipped earlier today; documents per-node observed performance vs the SOTA-doc recommendations; defines the next-step strategy.

**Cross-references:**
- [`KD-ROTATOR-BANDIT-SOTA-2026-05-23.md`](./KD-ROTATOR-BANDIT-SOTA-2026-05-23.md) — the bandit-core decision being validated here
- [`DD-PIPELINE-SOTA-COMPARISON-2026-05-23.md`](./DD-PIPELINE-SOTA-COMPARISON-2026-05-23.md) — the SOTA recommendations being re-ranked against observed data
- [`PLANNER-ARCHITECTURE-2026-05-17.md`](./PLANNER-ARCHITECTURE-2026-05-17.md) — the 8-node LITA hybrid being tested

## TL;DR

| | Outcome |
|---|---|
| **FGTS-VA bandit** | ✅ **VALIDATED** — zero matmul errors, 0% off_topic unparseable verdicts (was 8.1%), 5.6% refine errors (was 42%), 3.3× faster wall time |
| **Bandit cell learning** | ✅ Working correctly — variance-awareness correctly separated reliable workhorses (σ²<0.05) from noisy reasoning models (σ²>0.15); ~80% traffic concentrated on top-3 arms after 443 observations |
| **Plan structure** | ⚠️ **Still under-segmented** — 3 clusters → 2 chapters (after plan_write trimmed). Same structural issue as the shuffle-era run because clustering is deterministic w.r.t. inputs |
| **Recommended next step** | **Run Planner on LangChain stack** before applying any per-node tuning — validates whether cluster issues are systemic or small-corpus-specific |

## 1. FGTS-VA validation (before/after)

The previous Planner run on FastMCP (~19:00-19:14) was silently falling back to LiteLLM router-shuffle on every bandit call (924 `matmul: size 25 ≠ 24` warnings from stale Redis cells). After the cell wipe + `CellState.from_dict` hardening, the post-fix run (~19:19-19:27) shows the bandit actually firing:

| Metric | Pre-fix (shuffle) | Post-fix (FGTS-VA) | Delta |
|---|---|---|---|
| `matmul` errors during run | 924 | **0** | ✅ Bandit not falling back |
| `cell dim drift` graceful fallback warnings | n/a | 0 | ✅ Cells were wiped fresh; hardening untriggered (covers future dim changes) |
| off_topic `unparseable_verdict` errors | **27 / 335 (8.1%)** | **0 / 335 (0%)** | ✅ Bandit demoted unreliable arms |
| off_topic wall time | **653 s (10.9 min)** | **265 s (4.4 min)** | ✅ **2.5× faster** |
| refine errors | 5 / 12 (42%) on rerun | **1 / 18 (5.6%)** | ✅ ~7× lower error rate |
| refine wall time | 97 s | **18 s** | ✅ 5× faster |
| reduce wall time (variance) | 11s → 228s → 115s (~20×) | **66 s** (single pass, 2 repairs) | ✅ Stable |
| **Total Planner wall time** | **~25 min** | **~7.5 min** | ✅ **3.3× faster** |

## 2. Bandit cell distribution post-run

After 443 observations distributed across 18 (deployment, dd-grader) cells:

| Deployment | n_obs | σ²_ewma | Interpretation |
|---|---|---|---|
| `mistral/devstral-medium-latest` | 141 | 0.003 | **Most reliable** → got most traffic |
| `nvidia_nim/meta/llama-4-maverick-17b-128e-instruct` | 135 | 0.011 | Second-most reliable |
| `mistral/mistral-medium-latest` | 75 | 0.032 | Moderate noise, fewer picks |
| `nvidia_nim/z-ai/glm-5.1` | 16 | 0.178 | Higher noise (reasoning model `<think>` overhead) → demoted |
| `mistral/mistral-large-latest` | 14 | 0.162 | Same pattern |
| `nvidia_nim/openai/gpt-oss-120b` | 9 | 0.115 | Moderate noise |
| `nvidia_nim/nvidia/nemotron-3-super-120b-a12b` | 9 | 0.112 | Moderate noise |
| `nvidia_nim/qwen/qwen3.5-397b-a17b` | 8 | 0.182 | High noise |
| `mistral/magistral-small-latest` | 8 | 0.181 | High noise |
| `mistral/magistral-medium-latest` | 7 | 0.211 | High noise |
| `nvidia_nim/minimaxai/minimax-m2.7` | 4 | 0.222 | High noise, exploration tail |
| `mistral/mistral-small-latest` | 3 | 0.236 | High noise, exploration tail |
| `nvidia_nim/moonshotai/kimi-k2.6` | 3 | 0.184 | Reasoning, exploration tail |
| `nvidia_nim/mistralai/mistral-large-3-675b-instruct-2512` | 3 | 0.184 | Exploration tail |
| `nvidia_nim/deepseek-ai/deepseek-v4-flash` | 3 | 0.184 | Exploration tail |
| `gemini/gemini-3-flash-preview` | 2 | 0.290 | Highest noise (under-observed) |
| `gemini/gemini-2.5-flash` | 2 | 0.290 | Highest noise |
| `gemini/gemini-3.1-flash-lite` | 1 | 0.289 | Highest noise |

**Validation signals:**
- Per-arm σ² values are spread 0.003 → 0.290 (100× ratio) — strong heteroscedasticity. **This is exactly what FGTS-VA was designed for.** Plain LinTS with a fixed σ² couldn't differentiate these.
- Top-3 deployments captured 351/443 = **79% of all bandit picks** after the bandit identified them as low-noise.
- The bandit demoted reasoning models (kimi, glm, qwen) with `<think>` token overhead — appropriate for off_topic's KEEP/DROP judgment task.

## 3. Plan output

| | Run #1 (shuffle) | Run #2 (FGTS-VA) |
|---|---|---|
| Chapters in plan-latest.json | 3 | **2** (plan_write trimmed reduce's interim 4 → 2 valid) |
| Distribution | 252 / 72 / 7 | **256 / 70** + 6 unassigned + 2 dropped |
| Cluster count (HDBSCAN output) | 3 | **3** (unchanged — deterministic w.r.t. embeddings) |

Final plan structure:
- **Ch1: "FastMCP Core Architecture"** — 256 sources. Foundational design, components, server-side principles, includes skills providers.
- **Ch2: "Authentication and OAuth Integration"** — 70 sources. Third-party authentication workflows, OAuth, secure identity.

The bandit-driven LLM step improvements did NOT change the structural problem because clustering (UMAP + HDBSCAN) is deterministic for fixed embeddings and seeded UMAP. The bottleneck moved from "LLM call quality" to "input cluster granularity."

## 4. Per-node analysis & priority ranking

Ranked by **observed pain × ROI** after FGTS-VA validation:

| # | Node | Current state (FGTS-VA era) | Improvement | LOC | Priority |
|---|---|---|---|---|---|
| 🔥 1 | **`cluster`** | 3 clusters from 335 docs → lopsided 256/70 plan | HDBSCAN `min_cluster_size` tuning (adaptive: `max(4, n_docs // 70)` for small corpora) | ~10 | **Root cause of downstream structural issues** — but verify on bigger corpus first (see §6) |
| 🔥 2 | **`off_topic`** | 4.4 min wall time (60% of total runtime) | jina-reranker-v3 / NIM `nvidia/llama-nemotron-rerank-1b-v2` **listwise** rerank — replace 335 LLM calls with ~6 listwise batches | ~50 | **Biggest single time win available** — drops 4.4 min → ~10s |
| 3 | `embed_corpus` | 38-78 s "COLD" both runs (cache not hitting?) | Investigate cache miss (manifest hash should match across runs) + verify NIM exposes NV-Embed-v3 / Qwen3-Embedding-8B for embedder swap | ~10 + debug | Free observability win on cache; embedder swap depends on NIM availability |
| 4 | `reduce` | 66 s single-shot (was 11-228s with variance). 2 repairs. | None now — variance was the issue; FGTS-VA fixed it. "Kitchen sink Ch1" is upstream from reduce (it can't make 8 chapters from 3 clusters) | n/a | Fix #1 first; reduce will then operate on better clusters |
| 5 | `refine` | 5.6% error rate (was 42%). 18 s wall time | None now — FGTS-VA already fixed this | n/a | Socratic Self-Refine deferred — refine isn't pain anymore |
| 6 | `label` | 23 s, 3 USC-voted clusters, 0 errors | None | n/a | Works as designed |
| 7 | `plan_write` | 2 s, correctly trimmed reduce's overproduction | None | n/a | The "trim from 4 to 2" is a SYMPTOM of cluster being too coarse, not a plan_write bug |
| 8 | `corpus_load` | 0.4 s, 335 files | None | n/a | Fine |
| **Defer** | reduce backbone | Clio meta-clustering | HippoRAG 2 / SurveyG hybrid (the SOTA doc's "biggest gap") | ~500 | Big rewrite. Try cheap fixes (#1-#2) first; if structure is still bad after cluster tuning, revisit |

## 5. SOTA-doc recommendation re-ranking

Compared to the original ranking in [`DD-PIPELINE-SOTA-COMPARISON-2026-05-23.md`](./DD-PIPELINE-SOTA-COMPARISON-2026-05-23.md) §2:

| Original SOTA rank | Re-ranked by observed pain | Reasoning |
|---|---|---|
| #2 jina-reranker-v3 listwise | **#1** (off_topic still 60% of runtime even after FGTS-VA shaved 6.5 min) | Confirmed via direct measurement |
| (not in SOTA doc) | **#2** — HDBSCAN min_cluster_size tuning | Surfaced by observing under-segmentation. Cheap config change. |
| #1 embedder swap | #3 | Real win on MTEB v2 but secondary to runtime + structure problems. Plus needs NIM availability check |
| #3 Socratic Self-Refine | **defer** | Refine errors are *LLM-produced unparseable output*, not *reasoning errors*. FGTS-VA already addressed this |
| #4 HippoRAG 2 / SurveyG | **defer to Phase 5+** | Big rewrite; try cluster tuning first to see if the kitchen-sink problem resolves cheaper |

## 6. Next-step strategy: validate on bigger corpus FIRST

**Don't apply per-node tuning to FastMCP results yet.** Three reasons:

1. **The 3-cluster result might be correct for FastMCP.** With 335 docs across architecture + auth + skill-providers, 3 topical groups may be the actual ground truth. Forcing more clusters via `min_cluster_size=4` could fragment legitimate clusters into noise.

2. **Pipeline behavior changes at scale.** The Terragrunt baseline (per [[project_terragrunt_baseline_2026_04_30]]) hit "4/11 shard timeouts, 160-file junk-drawer Chapter 3" — a completely different failure mode than FastMCP's "3 clusters, lopsided." Small-corpus tuning could hurt large-corpus runs.

3. **Bandit data quality scales with observations.** FastMCP gave us 18 cells × 1-141 obs each. LangChain stack should give ~30-50 cells × 100-500 obs each, producing much sharper variance estimates and validating FGTS-VA at production scale.

**Recommended sequence:**

1. **Re-run Planner on a larger corpus** — LangChain stack (chains, agents, LCEL, runnables, etc.) or similar 2000+ doc framework
2. **Repeat this analysis** — produce parallel per-node metrics, compare to FastMCP run, identify which issues are systemic vs small-corpus-specific
3. **Apply cluster tuning ONLY if** the bigger-corpus run also produces under-segmented output (e.g., 4-5 clusters from 2000 docs); otherwise leave HDBSCAN alone
4. **Apply jina-reranker-v3 / NIM listwise rerank for off_topic** regardless — at LangChain scale, off_topic would be ~30 min on current architecture (335 docs → 4.4 min; 2000 docs → ~26 min). Listwise rerank stays ~10s. **The bigger the corpus, the bigger the win.**
5. **Investigate embed_corpus cache miss** in parallel — it should hit on identical manifests
6. **Reserve HippoRAG 2 / SurveyG for Phase 5+** — only if cluster tuning + listwise rerank don't produce balanced chapter plans at scale

## 7. Acceptance criteria for "ready to tune"

After the LangChain stack run, the per-node tuning should ship if-and-only-if:

| Signal | Threshold |
|---|---|
| Cluster count from HDBSCAN | <8 clusters for >1500 docs → tune `min_cluster_size` |
| Plan-latest chapter distribution | Top chapter has >40% of sources → over-concentrated; cluster fix needed |
| off_topic wall time | >15 min → listwise rerank required |
| Refine error rate | <10% → no change needed (FGTS-VA holds at scale) |
| Reduce variance | <5× across multiple runs → no change needed |
| Bandit cell count | <30 → bandit needs more dd_processes / more deployments active |
| FGTS-VA σ² spread | <50× across arms → heteroscedasticity weaker than expected, variance-aware advantage diminished |

If any signal exceeds threshold, the relevant per-node change ships next. If all signals are below threshold at LangChain scale, the current pipeline is production-ready for similar corpora.

## Sources

- [FGTS-VA — *Variance-Aware Feel-Good Thompson Sampling*, NeurIPS 2025](https://arxiv.org/abs/2511.02123)
- [Agrawal & Goyal — *LinTS*, ICML 2013](https://arxiv.org/abs/1209.3352)
- [LITA — LLM-assisted Iterative Topic Augmentation, Dec 2024](https://arxiv.org/html/2412.12459v1)
- [QualIT — Amazon Science, LLM-in-the-loop topic modeling](https://www.amazon.science/blog/unlocking-insights-from-qualitative-text-with-llm-enhanced-topic-modeling)
- [jina-reranker-v3 (Sep 2025)](https://arxiv.org/abs/2509.25085)
- [HippoRAG 2 — NeurIPS 2024 (deferred)](https://github.com/OSU-NLP-Group/HippoRAG)
- Current code: `/home/rafaelcoelho/Workbench/COELHONexus/apps/fastapi/domains/dd/planner/`
- Bandit code: `/home/rafaelcoelho/Workbench/COELHONexus/apps/fastapi/domains/llm/rotator/bandit/`
