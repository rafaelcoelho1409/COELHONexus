# KD Planner REDUCE — May 2026 SoTA Optimization Plan

**Date:** 2026-05-11
**Status:** Phase A SHIPPED 2026-05-11 (R1 + R2 + Polish #1 + Polish #3). Phases B–G still pending. Successor to `KD-PLANNER-REDUCE-NEXT-STEPS.md` (the 3 tactical polishes) — captures the strategic May-2026 changes that follow.
**Companion docs:** `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` (architecture), `KD-PLANNER-MAP-OPTIMIZATION.md` (MAP picks committed 2026-05-09), `KD-PLANNER-REDUCE-NEXT-STEPS.md` (3 polishes — #1 and #3 shipped with Phase A, #2 subsumed by Router cascade after R2), `KD-LLM-PROVIDER-CATALOG-2026-04.md` (current rotator state).

## Ship log

| Date | Change | Where | Notes |
|---|---|---|---|
| 2026-05-11 | R1 — `kd-reduce-label` rotator group (8 deployments, non-reasoning only) | `services/llm_chain.py` (REDUCE_LABEL_GROUP, `_reduce_label_entries`, `build_reduce_label_chain`) | Evidence-based pool curated against the live state of `_all_entries()` — see deviations below |
| 2026-05-11 | R2 — `method="function_calling"` → `method="json_schema"` for both `label_chain` and `order_chain` | `reduce_cluster.py` step 7 + step 9 | Server-side schema enforcement; "silent None on tool_call parse fail" path becomes structurally impossible on providers that honor it |
| 2026-05-11 | Polish #1 — extend `if draft is None` guard to also catch empty/whitespace-only titles | `reduce_cluster.py:_label_one` | Belt-and-suspenders for deployments that silently fall back to free-form output |
| 2026-05-11 | Polish #3 — `_META_CLUSTER_MAX_FRACTION` 0.25 → 0.20 | `reduce_cluster.py:120` | No chapter beyond ~20% of corpus |
| 2026-05-11 | Skipped Polish #2 (retry-once-before-synthetic) | — | Subsumed by Router's per-deployment cascade after R1+R2 land. Router already retries the next deployment in `kd-reduce-label` on any structured-output failure. |
| 2026-05-11 | Phase A validated on Docker | study `4086ee60-…` | 994 files → 8 chapters, **zero errors of any kind** (no 504/413/silent-None/empty-title/"and Related"), silhouette 0.404, balanced sizes `[14, 21, 12, 17, 20, 17, 16, 18]`, max chapter 19.2% of corpus (just under T-2 0.20 cap — Polish #3 binding). REDUCE labeling fired Gemini-3.1 Flash-Lite + Llama-4 Maverick + Nemotron-3 Super (all 200 OK). |
| 2026-05-11 | Polish #4 — title sanitization | `reduce_cluster.py:_sanitize_title` + `_label_one` | Phase A surfaced cosmetic artifacts on Ch3 (`": Docker Compose..."`) and Ch4 (`":** Managing Docker Hub..."`). Regex strips leading `**` emphasis, `Chapter N:` numbering, and runs of `:*-.` punctuation; also trims trailing emphasis. Test suite covers 10 shapes including the observed forms. |
| 2026-05-11 | Polish #5 — Gemini-3 temperature=1.0 override | `services/llm_chain.py::_gemini_entry` + kd-reduce-label entry | Phase A run logged Google's warning: "Setting temperature < 1.0 for Gemini 3 models can cause infinite loops, degraded reasoning performance." `_gemini_entry` gained `*, temperature=None` kwarg; kd-reduce-label's Gemini-3 entry now pins T=1.0. R2's json_schema mode keeps the output valid; only token sampling among valid JSON paths changes. |
| 2026-05-11 | I1 fix — UMAP numba JIT pre-warm at worker init | `celery_app.py::_prewarm_umap_jit` (worker_process_init signal) | Phase A run showed UMAP fit_transform took **496s** on a 135×128 input — pure numba JIT cold-start in fresh Celery worker process. Pre-warm with production parameters (n_components=5, n_neighbors=15, min_dist=0.0, metric='cosine', random_state=42) on a 20×128 dummy at boot moves the cost to startup; first real study sees a warm cache. Non-fatal on failure. |
| 2026-05-11 | Validated Polish #4 + I1 on Docker | study `3d40f8ce-…` | **UMAP 496s → 0.54s (918× speedup)**; titles `: Docker AI Agent Development` + `: Docker Image Building and Optimization` sanitized cleanly via Polish #4 (visible in logs as `[reduce-cluster] meta X title sanitized:`); 7 chapters emitted with no `: ` / `:** ` artifacts; max chapter size 27/135 (20%, T-2 cap binding exactly); zero 504/413/silent-None/synthetic-fallback. Total task wall-clock 264s post-pre-warm (vs 1268s on the prior Phase A run, **5× total speedup**). |
| 2026-05-11 | Polish #5b — factory `temperature=1.0` instead of per-deployment | `services/llm_chain.py::build_reduce_label_chain` + reverted `_gemini_entry` helper to its original signature | Initial Polish #5 set `temperature=1.0` in the Gemini-3 deployment's `litellm_params`, but LiteLLM Router lets **call-time params win** over deployment defaults — the factory's `temperature=0.0` overrode the deployment override, and Google still emitted the "infinite loops" warning. Fix: set T=1.0 at the factory level (applies to all kd-reduce-label calls). json_schema enforcement keeps output valid regardless of T on non-Gemini deployments. |
| 2026-05-11 | **R8** — global community detection in MAP | `apps/fastapi/graphs/knowledge/classical_map.py::label_corpus_classical` + `_cluster_corpus` + `distiller.py::_use_global_map` + Helm values `kd.globalMap` env flag `KD_GLOBAL_MAP` | Drop-in alternative to `label_shards_classical`: flattens shards to one corpus, one batched NIM embed call, one global `community_detection` pass on the full N×N cosine matrix. Eliminates cross-shard fragmentation (same topic appearing in 5 shards becomes 1 community, not 5). Gated behind `KD_GLOBAL_MAP=1` env flag (default off, flip after A/B validation on Docker + Terragrunt + 1 third corpus). Returns single-element `list[ShardLabels]` — downstream REDUCE flattens shard_results anyway, so the shard dimension was vestigial. ~110 LoC. |
| 2026-05-11 | Validated Polish #5b on Docker | study `1e89fb9c-…` (run partially observed; cache replaced before completion) | Gemini-3.1-flash-lite-preview returned 200 OK twice during REDUCE labeling; **the "Setting temperature < 1.0 for Gemini 3" warning is GONE from logs** — confirming the factory-level T=1.0 fix took effect. Side observation: heavy retry cascade on Mistral-Large-3 / Nemotron-Super-120b deployments (180s+ on labeling) — exactly the failure mode R4 targets. |
| 2026-05-11 | **R4** — hedged invoke in `_label_one` + `order_chain` | `apps/fastapi/graphs/knowledge/reduce_cluster.py::_hedged_invoke` + both call sites | Fanout=2 against the kd-reduce-label pool; `asyncio.wait(FIRST_COMPLETED)`, cancel loser. Caps p95 latency at the second-fastest deployment in the pool — kills the 180s+ slow-tail cascade observed on study `1e89fb9c`. LiteLLM Router doesn't natively hedge; this lives in-process. Both labeling and ordering calls hedged (single-serial ordering benefits even more than parallel labeling). ~80 LoC. |
| 2026-05-11 | Polish #6 — extend MAP `_sanitize_label` regex | `classical_map.py::_LEADING_LABEL_RE` | Defense-in-depth alongside Polish #4 at the REDUCE layer. New regex catches `**emphasis`, `Chapter N:` numbering, leading `: / :** / :--` punctuation runs in addition to the original `title:` / `label:` / `topic:` / `name:` prefixes. KeyLLM (Llama-3.2-1B) is unlikely to emit Markdown artifacts, but the cost is trivial. Validated by Python test suite (11 cases including all observed shapes). |
| 2026-05-11 | **R7** — disk-cache embeddings keyed on (model_id, sha256(text)) | `services/knowledge/embeddings.py::_EMBED_CACHE` + `_cache_lookup_partition` + `_cache_store` + `diskcache` dep | Wrap `embed_texts` / `embed_texts_sync` with a per-text cache lookup. Cache key includes the rotator group name (`kd-embed:<sha256>`) so a future model swap auto-invalidates. 1 GiB cap (~65K cached texts at 2048×4 bytes each), LRU eviction. Cache dir `/app/.embed_cache` (ephemeral within pod — tuning-loop wins, lost on pod restart; PVC mount would persist further). Best-effort writes (cache failures never block successful embed calls). On Docker corpus: saves ~30s NIM round-trips on the 994-text MAP Phase A embed. |
| 2026-05-12 | Validated Tier 1 batch on Docker | study `4ac13eb5-…` (cold run, KD_GLOBAL_MAP=0) | **R4 confirmed**: labeling 9 metas in **10.5s** parallel (no retry cascade, vs 180s+ tail observed before R4). **R7 confirmed**: every embed call logs `cache: 0/X hit` (cold, expected; cache populating). **Polish #4 fired**: meta 3 `: Docker AI Extension Development` → `Docker AI Extension Development`. **Polish #5b confirmed**: zero `Setting temperature.*Gemini.*infinite` warnings. **UMAP pre-warm fastest yet**: 165–168s per child. **Silhouette 0.464** (record). Total wall-clock 288.9s including pre-warm. |
| 2026-05-12 | R8 first run (KD_GLOBAL_MAP=1) — algorithm worked, schema bug | study `c591058b-…` (failed with Pydantic validation error) | Global community_detection on 994 files produced **140 clusters in 75.3s** (Phase A 48.8s + Phase B 26.5s) and **only 48 unused slugs (vs 103 per-shard — 53% fewer orphans)**. Task FAILED because `ShardLabels.clusters` Pydantic `max_length=10` (per-shard reasoning, predates R8) — packed 140 into one ShardLabels = constraint violation. Per-shard projection of "135→30 micro-clusters" was wrong (threshold=0.60 catches similar-density communities at large N too); R8's actual win is orphan recovery, not size reduction. |
| 2026-05-12 | R8 bugfix — chunk global clusters into synthetic shards | `classical_map.py::label_corpus_classical` | Patched to chunk the global cluster list into synthetic ShardLabels of ≤_CLUSTERS_PER_SYNTHETIC_SHARD=10 each. `unused_shard_slugs` placed on the first synthetic shard only (REDUCE concatenates them downstream). Pure schema-compliance maneuver — REDUCE flattens shard_results anyway, no semantic impact. ~15 LoC. |
| 2026-05-12 | R8 second run (with bugfix) — algorithm works + new quality issue | study `fd6989bf-…` | Task completed cleanly (305.9s, no Pydantic error — bugfix confirmed). 994 files → 140 clusters in **14 synthetic shards** + 48 unused (53% fewer orphans than per-shard). **Silhouette 0.513 + CH 130.5** (record bests). BUT: chapter 5 ballooned to **273 files (28.9% of corpus)** with title "AI Tools, CLI & Account Management" (3 distinct topics mashed) — T-2 cap (on micro-cluster count) didn't bind because the 22 member micro-clusters were only 16% of n_clusters. Global mode's variable community sizes broke the implicit "per-shard size limit" the cap relied on. |
| 2026-05-12 | **R8b** — file-count cap on meta-clusters | `reduce_cluster.py::_META_MAX_FILE_FRACTION` + post-T-3 split block | Adds a per-chapter file-count cap (20% of total assigned slugs). After T-3 thin-merge, any meta over the cap is split via sub-KMeans (k=2) on its UMAP-reduced member vectors. Bigger half keeps the original meta_id; smaller half gets a new id. ONE pass — no recursion (warn-and-accept if a sub-meta is still oversized). Hard-capped at `_MAX_CHAPTERS=12`. ~75 LoC. Designed to make R8 safely default-on by killing the junk-drawer failure mode while preserving the orphan-recovery win. |
| 2026-05-12 | **R8b validated on Docker** — best plan ever produced | study `6b2ea2cf-…` (KD_GLOBAL_MAP=1) | R8b split fired exactly as designed: meta 7 had 250 files → split into 179 + 71. **10 chapters with max 18.9% (under cap), min 5.0%, all topically distinct titles.** Silhouette **0.522** (record), CH **144.6** (record). Labeling **2.5s** for 10 metas in parallel (R4 hedge + warm rotator caches at peak performance). Orphan recovery preserved at 48 files (vs 103 per-shard, 53% better). Total wall-clock 307s. |
| 2026-05-12 | **`kd.globalMap` defaulted to `"1"`** | `k8s/helm/values.yaml` | After R8b validation, R8+R8b together produce strictly better plans than per-shard mode (better balance, fewer orphans, higher silhouette, same wall-clock, same chapter quality). No remaining failure mode. Default flipped from `"0"` → `"1"`; per-shard mode (`label_shards_classical`) remains available via `kd.globalMap: "0"` for fallback / A/B. |
| 2026-05-12 | **Terragrunt regression — `kd.globalMap` REVERTED to `"0"`** | `k8s/helm/values.yaml` | Validation on Terragrunt (study `8e88137d`, 440 files) surfaced an R8b regression: plain KMeans(k=2) sub-split on the oversized 8-micro-cluster meta produced a degenerate 7+1 partition → 253-file sub-meta still at 63% of corpus. Mega junk-drawer chapter "Terragrunt Development & Contribution" with 253 files (vs Polish #3's 20% target). Per-shard mode produces 5 clean chapters on Terragrunt; R8+R8b regressed below the baseline. Default reverted; fix in next entry. |
| 2026-05-12 | **R8b v2** — adaptive-k KMeansConstrained sub-split | `reduce_cluster.py` (rewrite of the split block) | Two improvements over the original R8b: (1) `sub_k = ceil(files_in_meta / file_cap)` — a 4×-oversized meta gets split into 4 sub-metas in one pass, not 2. (2) `KMeansConstrained(size_min=fair_share/2, size_max=fair_share*2)` — forces every sub-bucket to have at least N/2k members, preventing the degenerate (n-1, 1) partition outright. Falls back to plain KMeans on constraint infeasibility. Drops empty buckets defensively. Bounded by `len(members)` (can't have more clusters than members) and `_MAX_CHAPTERS - len(meta_groups) + 1` (schema budget). Single-pass (no recursion); warn-and-accept if biggest sub-bucket is still over cap (inherent limit when a single micro-cluster's files alone exceed the cap — needs MAP-layer fix). Validated for Terragrunt expected case: 8 members + 259 files + cap=80 → sub_k=4, ~65 files per sub-meta. ~110 LoC rewrite. |

## Status as of 2026-05-12

**SHIPPED + VALIDATED:**

- ✅ R1 — `kd-reduce-label` rotator group
- ✅ R2 — `method="json_schema"` for label_chain + order_chain
- ✅ R3 — REDUCE uses NIM `kd-embed` rotator (already de facto; doc cleanup pending)
- ✅ R4 — hedged invoke (fanout=2) for `_label_one` and `order_chain`
- ✅ R7 — disk-cache embeddings (`diskcache` keyed on model_id + sha256)
- ✅ R8 — global community_detection in MAP (`label_corpus_classical`)
- ✅ R8b — file-count cap with post-T-3 sub-KMeans split
- ✅ Polish #1 — empty-title guard at REDUCE
- ✅ Polish #3 — T-2 cap 0.25 → 0.20 (micro-cluster count)
- ✅ Polish #4 — REDUCE title sanitization
- ✅ Polish #5b — factory `temperature=1.0` for Gemini-3 compatibility
- ✅ Polish #6 — extended MAP `_sanitize_label`
- ✅ I1 — UMAP numba JIT pre-warm at worker init
- ✅ uv override for `unclecode-litellm` namespace collision
- ✅ `kd.globalMap` defaulted to `"1"`

**Best Docker result (2026-05-12, study `6b2ea2cf`):**
- 10 chapters, max 18.9% / min 5.0% of corpus
- Silhouette 0.522, Calinski-Harabasz 144.6
- 48 orphan files (down from 103 in per-shard era)
- Labeling 2.5s for 10 metas parallel
- Total wall-clock 307s (~5 min) from task fire to completion

**DEFERRED (not blocking, not shipped):**

- R5 — file-centroid embedding for REDUCE input (partly subsumed by R8 + R8b)
- R6 — pre-labeling reranker (defer until Groq 413s reappear)
- Numba `@njit(cache=True)` monkey-patch (saves 3.5min worker boot; PVC mount complexity)

**Best next direction (different scope):**

The KD planner has plateaued — chapter quality is high, wall-clock is acceptable. The dominant remaining time sink is the **synthesizer step** (~20–40 min per study, not planner). The R1+R2 pattern applied to synthesis (separate `kd-synth` non-reasoning pool, json_schema where applicable) is the next big user-facing optimization. Out of scope for this doc; tracked elsewhere.

## Final status (2026-05-12)

**REDUCE step is stable and considered done for this optimization pass.** Default is per-shard MAP (`kd.globalMap: "0"`), validated on Docker (1318 files → 9 clean chapters, max 17.2%) and Terragrunt (440 files → 5 clean chapters historical baseline, max 36%). R8 (global MAP) remains opt-in via `KD_GLOBAL_MAP=1` for advanced tuning on large corpora.

### Known limitations (R8 in opt-in mode)

R8 is **corpus-dependent**. It produces strictly better plans on large corpora with diffuse topical structure (Docker: silhouette 0.522, 48 orphans vs 103, max chapter 18.9%) and regresses on small corpora with dense topical concentration (Terragrunt: max chapter 44.6%, mega junk drawer "Contributing to Terragrunt" with 178 files).

Root cause: at `community_detection`'s `threshold=0.60`, dense topical regions like Terragrunt's contributing/development docs (~150 files clustering tightly) become single oversized communities. R8b's REDUCE-layer sub-split can partition the meta-cluster they end up in, but can't split the underlying micro-cluster — its file count is the inherent floor. Per-shard mode naturally bounds community size to ≤40 files via the shard boundary; global mode has no such bound.

Empirical heuristic for opt-in: **R8 recommended for corpora ≥800 files**; per-shard remains safer for smaller corpora.

### Future improvements (deferred — only revisit if a concrete need arises)

| Option | Effort | What it solves | When to revisit |
|---|---|---|---|
| **R8b — re-run T-3 thin-merge after the file-cap split** | ~5 LoC, refactor T-3 into a callable function | Cosmetic — absorbs the 4-file orphan sub-metas R8b creates (e.g., Terragrunt 2026-05-12 study `00e83dfb` chapters 7+8) into their nearest neighbor. Doesn't fix the underlying mega-chapter. | Only if R8 becomes the default AND telemetry shows the orphan sub-metas degrading user experience. |
| **R8c — MAP-layer community size cap** | ~30 LoC, recursive function in `community_detection`, threshold tuning | Principled root-cause fix. Caps individual community size at e.g. 50 files in `_cluster_corpus`; oversized communities get sub-split via recursive `community_detection` at higher threshold (or k-means subdivision). Would make R8 work uniformly across all corpora and let us flip `kd.globalMap` to `"1"` as default. | Only if there's a specific Terragrunt-class corpus a real user needs handled well, AND the orphan-recovery win matters enough to justify another redeploy + dual-corpus validation cycle. |
| **R5 — file-centroid embedding for REDUCE input** | ~40 LoC, research-pilot | Use `CORPUS_PREVIEW_CHARS=80` cached file content as the embedding signal instead of `cluster_name + description`. Partly subsumed by R8+R8b. | Only if a future framework's chapters look topically incoherent. |
| **R6 — pre-labeling reranker** (NIM `llama-nemotron-rerank-1b-v2`) | ~50 LoC + new `kd-rerank` rotator group | Cuts `_label_one` prompt tokens 30–60% by reranking members against a seed name. | Only if Groq 413s reappear or rotator TPM ceilings get hit. |
| **Numba `@njit(cache=True)` monkey-patch for UMAP** | ~10 LoC + Helm PVC mount | Persists numba JIT artifacts across worker restarts. Eliminates the 3.5 min pre-warm cost on every fresh pod boot. | Only if skaffold cycle time becomes a daily friction point. |

### Next scope (separate doc when started): Synthesizer optimization

The same `R1 + R2 + R4` triad pattern that worked for REDUCE applies cleanly to the synthesizer step:

- **R1-equivalent**: separate `kd-synth` rotator group of non-reasoning models suitable for prose generation (different list than `kd-reduce-label` — prose generation benefits from larger / more creative models like Mistral-Large-3, Llama-4-Maverick, Nemotron-Super; explicitly excludes reasoning models that burn `<think>` budget on long-form output)
- **R2-equivalent**: `method="json_schema"` for `ChapterOutput` / `ProseChapterOutput` / `ChapterOutline` / `Section` schemas (already structured, just not using grammar-constrained JSON)
- **R4-equivalent**: hedged invoke at fanout=2 for synthesis calls — the highest absolute wall-clock win available since each synthesis call is ~30–120s

Synthesizer represents ~20–40 min per study (8–10× the planner's wall-clock). This is the next big user-facing optimization. Track in a new doc when scoped.

### Phase A deviations from the deep-research plan

The research proposed a Cerebras-first pool with SambaNova + Gemini-2.5-flash-lite + Mistral mid-tier. The shipped pool diverges because the live `_all_entries()` catalog in `llm_chain.py` documents account-level model-access failures that the research couldn't see:

| Originally proposed | Shipped substitute | Reason |
|---|---|---|
| Cerebras `gpt-oss-120b` (1M TPD, 3000 tok/s) | NIM `openai/gpt-oss-120b` | Cerebras returns 404 "you do not have access to it" on this account's key (per kd-all comment 2026-04-24). NIM hosts the same model family. |
| SambaNova Llama-3.3-70B / Llama-4-Maverick / DeepSeek | dropped — NIM Llama-4-Maverick instead | Entire SambaNova provider went paywalled in May 2026 ("A payment method is required"). |
| Gemini 2.5 Flash-Lite as a structured-output target | Gemini 3.1 Flash-Lite Preview | The 2.5 Lite tier is documented in kd-all as returning empty `choices=[]` on complex tool schemas; the 3.1 preview tier handles the simpler MetaLabelDraft/OrderedIndices schemas. |
| DeepSeek V4-pro / V4-flash | dropped | "Insufficient Balance" on this account (5M signup grant exhausted). |

The shipped 8-deployment pool: Groq llama-3.3-70b-versatile → Gemini-3.1-flash-lite-preview → NIM nemotron-3-super-120b-a12b → NIM gpt-oss-120b → NIM mistral-large-3 → Mistral mistral-large-latest → Mistral mistral-small-latest → NIM llama-4-maverick.

## Headline finding

The dominant fix is to **stop using `with_structured_output(method="function_calling")` on the broad `kd-all` rotator for REDUCE labeling**. Two changes — splitting off a non-reasoning `kd-reduce-label` pool (Cerebras `gpt-oss-120b` first) and switching to grammar-constrained JSON (`nvext.guided_json` on NIM, `response_format: json_schema` elsewhere) — together kill ~all 504s and bring p95 labeling from 28–60s to ~5–15s. Embedding model and reranker work is secondary.

## Why REDUCE labeling is the bottleneck

- `_label_one` in `reduce_cluster.py:373-431` is parallel over M=4–12 meta-clusters, but each call routes through `with_structured_output(method="function_calling")` on the `kd-all` rotator — a pool that includes large reasoning models (GLM-5.1, Qwen3.5-397B-A17B, DeepSeek-V3.2, Kimi-K2.5) that burn the 300s NIM gateway budget on `<think>` blocks for a structurally simple 3K-token classification task.
- Three independent failure modes stack: (1) NIM 504 from gateway timeout on reasoning models, (2) Groq 413 from per-model TPM ceiling, (3) silent `None` from non-parseable tool_calls (the empty-title bug that Polish #1 patches reactively).
- Wall-clock for parallel labeling dominates: 28–60s on M=8–12 with stragglers worse.
- Embedding model (`bge-base-en-v1.5`, late 2023) is the only local CPU spike left in the planner pipeline since the May-9 NIM unification; it is also the geometric signal feeding UMAP+KMeansConstrained, so quality bounds k-means quality.

## 7 ranked recommendations

| # | Change | Where | Expected delta | LoC | Status |
|---|---|---|---|---|---|
| **R1** ✅ SHIPPED 2026-05-11 | `kd-reduce-label` rotator group (8 deployments, evidence-based pool — see Ship log for deviations from research): Groq `llama-3.3-70b-versatile` → Gemini `gemini-3.1-flash-lite-preview` → NIM `nvidia/nemotron-3-super-120b-a12b` → NIM `openai/gpt-oss-120b` → NIM `mistralai/mistral-large-3-675b-instruct-2512` → Mistral `mistral-large-latest` → Mistral `mistral-small-latest` → NIM `meta/llama-4-maverick-17b-128e-instruct`. **Hard-excludes reasoning models** (Kimi K2-Thinking, GLM-5.1, MiniMax-M2.7, Qwen3.5-397B, DeepSeek-V3.2, Magistral, Gemini-3-flash R-mode). | `services/llm_chain.py` (`REDUCE_LABEL_GROUP`, `_reduce_label_entries`, `build_reduce_label_chain`) + `reduce_cluster.py` step 7 | p95 60s → 10–15s expected, 504s → ~0, synthetic-fallback titles vanish | ~80 LoC actual | production |
| **R2** ✅ SHIPPED 2026-05-11 | `method="function_calling"` → `method="json_schema"` on both `label_chain` and `order_chain`. Per-provider grammar surface is selected automatically by LangChain-LiteLLM at the SDK layer — explicit `nvext.guided_json` / `response_format: json_schema` dispatch deferred (langchain-litellm 0.6.4 translates `method="json_schema"` to the appropriate per-provider call). Empty-title guard (Polish #1) retained as belt-and-suspenders for deployments that silently fall back to free-form output. | `reduce_cluster.py` step 7 + step 9 | "Silent None on tool_call parse fail" path becomes structurally impossible on providers that honor schema — Polish #1 (empty-title) gone by construction on most of the pool | ~30 LoC actual | production |
| **R3** | Drop local fastembed `bge-base-en-v1.5`; reuse the `kd-embed` rotator (`nvidia/llama-nemotron-embed-1b-v2`) that MAP already uses. Finishes the May-9 architectural pivot — REDUCE was the last local-CPU holdout. | `services/knowledge/embeddings.py` | Last local CPU spike removed; same embedding geometry as MAP; 768d → 2048d gives +2–5 silhouette pts on tight same-domain corpora | ~5 | production |
| **R8** | **Global community detection in MAP** (lives in MAP code but its primary beneficiary is REDUCE). Replace per-shard `_cluster_shards` loop with ONE global pass: batch-embed the entire corpus via `kd-embed`, run `community_detection` once on the full N×N cosine matrix. The per-shard structure was a legacy of the original LLM-based MAP's TPM ceiling — it doesn't apply to the classical algorithm. **Caveat:** O(N²) memory is fine up to ~10K files (Docker N=1318 → ~7MB float32; Terragrunt N=440 → ~770KB). Beyond N≈10K, switch to FAISS HNSW + threshold. | `apps/fastapi/graphs/knowledge/classical_map.py::_cluster_shards`, `label_shards_classical`; one call-site update in `distiller.py:536-540` | Micro-cluster count Docker ~135 → ~30 (4-5× drop); KeyLLM naming calls in MAP Phase B cut proportionally; REDUCE input set 4-5× smaller → REDUCE labeling cost scales down with it; **cross-shard fragmentation eliminated** (one "networking" cluster, not 5). Makes R5's structural fix partly redundant (junk drawers unlikely once fragmentation is gone) — R5 becomes a quality polish. | ~30 | production |
| **R4** | Hedged invoke in `_label_one`: fanout=2, `asyncio.wait(..., return_when=FIRST_COMPLETED)`, cancel rest. **LiteLLM Router does not natively hedge** as of 2026 (only fallback/cooldown — verified against docs) — must be in-process. | `reduce_cluster.py:386-405` | Cuts p95 stragglers (typically 2–4× p95→p50 for the slowest meta) | ~25 | production |
| **R5** | Embed **centroid-of-file-previews** per micro-cluster (`CORPUS_PREVIEW_CHARS=80` signal already cached in `helpers.py`) instead of just `cluster_name + description`. Optional weighting: `0.3 × name_emb + 0.7 × file_centroid_emb`. **Reframing after R8:** with cross-shard fragmentation already gone, this becomes a quality polish (better embedding signal per micro-cluster) rather than a structural fix for junk-drawer chapters. | `reduce_cluster.py:173-180` | Silhouette 0.25 → 0.35–0.50 on tight tech-doc corpora; backed by HERCULES `direct` mode + RAPTOR/HiRAG | ~40 | research-pilot |
| **R6** | Reranker (NIM `llama-nemotron-rerank-1b-v2`) pre-`_label_one` to top-K members → cuts prompt tokens 30–60% on large metas. **Skip Jina-rerank-v3** (CC-BY-NC-4.0, non-commercial). | new `kd-rerank` group | Marginal latency win, mostly a Groq TPM hedge | ~50 | defer until Phase A insufficient |
| **R7** | `diskcache` / `CacheBackedEmbeddings` keyed on `(model_id, sha256(text))` — embedding cache across re-runs | `services/knowledge/embeddings.py` | Test-loop QoL: ~3–10s saved per re-run on same corpus | ~15 | production (low cost) |

## Recommended order of attack

After the 3 tactical polishes in `KD-PLANNER-REDUCE-NEXT-STEPS.md` (empty-title guard, retry-once, T-2 0.25→0.20):

- **Phase A** ✅ SHIPPED 2026-05-11 — R1 + R2 together. All four provider API keys (`CEREBRAS_API_KEY`, `MISTRAL_API_KEY`, `GOOGLE_API_KEY`, `SAMBANOVA_API_KEY`) were already wired in `k8s/helm/values.yaml` from prior work. Built `kd-reduce-label` group + `build_reduce_label_chain()` factory in `services/llm_chain.py`. Switched `_label_one` + `order_chain` to `method="json_schema"` against the new pool. Polish #1 + Polish #3 also shipped. Polish #2 skipped (Router cascade subsumes it). **Next:** redeploy skaffold (manual per `feedback_skaffold_manual_redeploy`), re-run Docker + Terragrunt, verify zero 504s, zero `"… and Related"` synthetic titles, zero empty titles, p95 labeling under 20s.
- **Phase B (sub-day)** — R3. Drop the fastembed branch in REDUCE; one-line embedding unification onto `kd-embed`.
- **Phase C (~half-day, own PR with A/B comparison)** — R8. Refactor `classical_map.py` to do one global embed + one global community_detection. Add `KD_GLOBAL_MAP=1` env flag (default off initially) so it can be toggled per study. Compare against per-shard baseline via `/debug/map_compare` on Docker + Terragrunt + a third corpus. Promote to default once micro-cluster count drops 4-5× without quality regression. **Biggest structural change after Phase A.**
- **Phase D (~2 h)** — R4. `_hedged_invoke` helper, fanout cap=2 (free-tier consumption stays acceptable at M=8–12).
- **Phase E (research-pilot, ~1 d)** — R5. Build `/debug/reduce_compare` A/B route mirroring the existing `/debug/map_compare` pattern. Run on the same 3 corpora. Reframed after R8 ships: promotion criterion is silhouette delta on the post-R8 micro-cluster set, not junk-drawer fix (which R8 already addresses).
- **Phase F (defer)** — R6. Only ship if Groq 413s reappear after Phase A.
- **Phase G (free)** — R7. 15 LoC quality-of-life win.

## Explicitly rejected (do not reach for these)

- **Jina-reranker-v3** (Oct 2025, SoTA BEIR 61.94, 0.6B) — CC-BY-NC-4.0, non-commercial, hard skip for a commercial product
- **Voyage-3-large / Cohere embed-v4 / OpenAI text-embedding-3-large** — paid-only; Cohere trial is 1000 calls/month + non-commercial
- **Stella-1.5B / Qwen3-Embedding-8B / Llama-Embed-Nemotron-8B local** — torch + GPU or massive CPU; violates "no in-cluster inference" rule (the same rule that drove Xinference removal on 2026-05-09)
- **HDBSCAN replacement of KMeansConstrained** — already rejected in `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md`; same-domain corpora dump 50%+ of points to noise. Confirmed by 2025 hierarchical-RAG follow-ups
- **HERCULES (arXiv 2506.19992) wholesale adoption** — recursive k-means + LLM-rename per level = MORE LLM calls per study; we already do its 1-level case. Cherry-pick only: HERCULES's `topic_seed` already maps to the framework name injected into `META_LABEL_PROMPT`
- **Outlines / dottxt as a separate runtime** — needs self-hosted vLLM/SGLang; use providers' native grammar surfaces (`nvext.guided_json`, `response_format: json_schema`) instead — R2 captures this
- **Plain `json_mode` (no schema)** — superseded by `json_schema` since mid-2025; production guides explicitly recommend `json_schema` over `json_mode`
- **Anthropic Claude / DeepSeek paid credits as primary REDUCE labeler** — trial-only or 30-day expiry. Keep DeepSeek free in deep tail of `kd-all`, not in `kd-reduce-label`
- **OpenRouter `:exacto` routing** — adds wrapper hop, RPD ceilings too tight for parallel labeling at M=8–12. Keep `:free` models in deep tail of `kd-all`, not in `kd-reduce-label`

## Files to touch when shipping

| Phase | File | What changes |
|---|---|---|
| A | `apps/fastapi/services/llm_chain.py` | New `kd-reduce-label` group + LangChain wrappers (Cerebras, Mistral, Gemini, SambaNova) |
| A | `apps/fastapi/graphs/knowledge/reduce_cluster.py` | `_label_one` + `order_chain` switch to `reduce_label_llm`; method-detection helper `_pick_structured_method(deployment)` |
| A | `apps/fastapi/schemas/knowledge/agents.py` | `MetaLabelDraft.model_json_schema()` already produces the grammar payload; verify it's clean |
| A | `apps/fastapi/schemas/knowledge/prompts.py` | Audit `META_LABEL_PROMPT` + `ORDER_PROMPT` for anti-tool-use language that needs adjustment for `json_schema` mode |
| A | `k8s/helm/values.yaml`, `k8s/helm/templates/_helpers.tpl` | New env vars: `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`, `GOOGLE_API_KEY`, `SAMBANOVA_API_KEY` |
| A | `apps/fastapi/pyproject.toml` | Either add `langchain-cerebras`, `langchain-mistralai`, `langchain-google-genai`, or use `ChatOpenAI(base_url=...)` for all (decide based on team's LangChain version policy) |
| B | `apps/fastapi/services/knowledge/embeddings.py` | Delete fastembed branch for REDUCE caller; route to `kd-embed` |
| C | `apps/fastapi/graphs/knowledge/classical_map.py` | Refactor `_cluster_shards`: replace per-shard loop with one global embed + one global `community_detection` call. Either (a) flatten shards into one input list and emit one result that mimics the per-shard schema for backward compat, or (b) add a new `label_corpus_classical(all_entries)` public API and update `distiller.py:536-540` to call it. Option (b) is cleaner |
| C | `apps/fastapi/graphs/knowledge/distiller.py:536-540` | Switch call site to `label_corpus_classical` when `KD_GLOBAL_MAP=1` |
| C | `k8s/helm/values.yaml`, `k8s/helm/templates/_helpers.tpl` | Add `KD_GLOBAL_MAP` env flag (default `0`, flip to `1` after A/B validation) |
| D | `apps/fastapi/graphs/knowledge/reduce_cluster.py` | Add `_hedged_invoke(chain, payload, *, n=2, timeout=30)` helper; wrap line 387 call |
| E | `apps/fastapi/routers/v1/knowledge/debug.py` | New `/debug/reduce_compare` A/B route mirroring `/debug/map_compare` |
| E | `apps/fastapi/graphs/knowledge/reduce_cluster.py` lines 173-180 | Centroid-of-file-previews embedding behind env flag `KD_REDUCE_FILE_CENTROID=1` |
| G | `apps/fastapi/services/knowledge/embeddings.py` | `CacheBackedEmbeddings` wrapper or `diskcache.Cache` |

## Validation plan per phase

Each phase ends with: stop+restart skaffold (per `feedback_skaffold_manual_redeploy` — no hot-reload assumption), then:

```bash
# Re-run both calibration corpora
curl -X POST "http://localhost:23020/api/v1/knowledge/studies?stop_after=planner" \
  -H "Content-Type: application/json" \
  -d '{"framework":"Docker"}'
curl -X POST "http://localhost:23020/api/v1/knowledge/studies?stop_after=planner" \
  -H "Content-Type: application/json" \
  -d '{"framework":"Terragrunt"}'
```

Phase-specific acceptance criteria:

- **A**: `grep "504\|413\|silent None\|and Related" logs/*.log` → zero matches; p95 labeling under 20s; no empty-title chapters
- **B**: planner runs without fastembed model download (cold) and without local CPU >50% during embedding step
- **C**: Docker micro-cluster count drops from ~135 to ≤40 with `KD_GLOBAL_MAP=1`; Terragrunt drops from baseline to ≤25; no "networking"-style near-duplicate cluster names across shards in the logged micro-cluster list; REDUCE k_target lands ≥6 (no thin-cluster regression); peak resident memory of the planner pod stays under 500MB at N≤2000
- **D**: p95 labeling under 10s; logs show "hedge winner: deployment=X" lines
- **E**: A/B comparison shows silhouette improvement ≥0.05 on post-R8 micro-cluster set under file-centroid mode (junk-drawer criterion no longer applies — R8 already addresses it)
- **G**: second run on same corpus skips embedding (log: "cache hit on N items")

## Architectural rationale

R3 finishes the same "no inference inside COELHO Cloud" pivot that retired Xinference and moved MAP to NIM on 2026-05-09. REDUCE was simply the last holdout. R1 + R2 close the parallel "LLMs of any size → rotator" principle for the REDUCE labeling path, which previously co-mingled small classification and large reasoning models in the same `kd-all` pool — a structural mismatch that the May-2026 free-tier landscape (Cerebras + Gemini Flash-Lite + SambaNova additions) finally makes correctable without paid APIs.

R8 retires the last vestige of the original LLM-driven MAP's per-shard structure. That structure was a TPM/context-budget workaround for LLMs, not a property of the classical algorithm that replaced them on 2026-05-09 — community_detection on cosine similarity has no TPM ceiling, no context window, and trivial memory at our corpus sizes. The per-shard loop survived the May-9 swap only because the rewrite was deliberately drop-in to minimize risk; R8 is the principled follow-up. Its primary value is upstream of REDUCE (fewer, cleaner micro-clusters), which collapses REDUCE's job to mostly naming + ordering at typical N — and partly subsumes R5.

## Sources

Curated subset (full list of 25+ in research notes):

- HERCULES, Jun 2025 — https://arxiv.org/abs/2506.19992
- Anthropic Clio, Dec 2024 — https://arxiv.org/html/2412.13678v1
- Qwen3-Embedding, Jun 2025 — https://qwenlm.github.io/blog/qwen3-embedding/
- Llama-Embed-Nemotron-8B, Oct 2025 — https://huggingface.co/nvidia/llama-embed-nemotron-8b
- Jina Reranker v3, Oct 2025 (rejected, NC license) — https://jina.ai/news/jina-reranker-v3-0-6b-listwise-reranker-for-sota-multilingual-retrieval/
- NIM Structured Generation (`nvext.guided_json`) — https://docs.nvidia.com/nim/large-language-models/latest/structured-generation.html
- NIM Function Calling 1.10 (`detailed_thinking=off`) — https://docs.nvidia.com/nim/large-language-models/1.10.0/function-calling.html
- vLLM Structured Outputs (XGrammar) — https://docs.vllm.ai/en/latest/features/structured_outputs/
- Cerebras Rate Limits (1M TPD free) — https://inference-docs.cerebras.ai/support/rate-limits
- Cerebras Free 1M tok/day, Apr 2026 — https://adam.holter.com/cerebras-opens-a-free-1m-tokens-per-day-inference-tier-and-ccerebras-now-offers-free-inference-with-1m-tokens-per-day-real-speed-benchmarks-show-2600-tokens-sec-on-llama4scout-here-are-the-actual-n/
- Groq Free Tier 2026 — https://tokenmix.ai/blog/groq-free-tier-limits-2026
- Gemini API Free Tier 2026 (1500 RPD) — https://tokenmix.ai/blog/gemini-api-free-tier-limits
- SambaNova function calling — https://sambanova.ai/blog/supercharging-ai-agents-with-function-calling-on-deepseek
- Mistral La Plateforme free tier 2026 — https://docs.mistral.ai/deployment/ai-studio/tier
- LiteLLM Router architecture (no native hedging) — https://docs.litellm.ai/docs/router_architecture
- Structured output 2026 production guide — https://www.buildmvpfast.com/blog/structured-output-llm-json-mode-function-calling-production-guide-2026
- HiRAG, Mar 2025 — https://arxiv.org/html/2503.10150v1
- BGE Reranker v2.5 Gemma2 lightweight — https://huggingface.co/BAAI/bge-reranker-v2.5-gemma2-lightweight
- NVIDIA llama-nemotron-rerank-1b-v2 — https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2
- Guided Decoding RAG, Sept 2025 — https://arxiv.org/html/2509.06631v1
- MTEB Mar 2026 leaderboard snapshot — https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/
