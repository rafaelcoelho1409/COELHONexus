# Synth Speed SOTA + Compliant Ship Plan (2026-05-26)

Wall-time audit of the current 8-node Synth pipeline against May 2026 state-of-the-art speed techniques, with a priority-ranked plan calibrated to the free-tier-only / no-local-inference constraint and `feedback_kd_quality_over_speed` (quality MUST NOT regress).

**Cross-references:**
- [`KD-SYNTH-SOTA-2026-05-24.md`](./KD-SYNTH-SOTA-2026-05-24.md) — quality-focused audit (pairwise picker, LettuceDetect, book_harmonize) — this doc is the **speed-focused sibling**
- [`KD-ROTATOR-BANDIT-SOTA-2026-05-23.md`](./KD-ROTATOR-BANDIT-SOTA-2026-05-23.md) — FGTS-VA bandit is the rotator surface every speed plan goes through
- [`SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`](./SYNTH-ARCHITECTURE-SOTA-2026-05-18.md) — pipeline shape being optimized

**Empirical trigger:** A single chapter takes ~30–50 min single-pass and up to 90+ min with refine loops. Two consecutive 0.69 checklist failures (Claude Code ch-01, Browser Use ch-01) confirm the algorithm is on the frontier but the wall-time is unacceptable for iteration speed.

## 1. TL;DR

| Rank | Ship | Effort | Per-chapter speedup | Risk | Rotator impact |
|---|---|---|---|---|---|
| 🥇 **1** | **Constrained decoding / structured outputs on SAWC + judge Pydantic schemas** | 3–4h | **10–25%** | Very low | None — per-call flag |
| 🥈 **2** | **Heterogeneous role-routing via arm-pool enrichment** (Cerebras / Groq / Gemini Flash 1M-ctx added to existing rotator names) | 1 day | **2–3×** | Low | None — same rotator names, more arms |
| 🥉 **3** | **NIM EAGLE-3 / Nemotron-MTP model IDs added as new arms** | 2–4h | **2–6× on nodes hitting MTP arms** | Very low | None — config-level |
| 4 | **Optimal-Stopping Best-of-N replacing fixed N=2 SAWC drafts** | 4–6h | **10–20%** | Low | None |
| 5 | **CISC (Confidence-Informed Self-Consistency) replacing MAMM critic-picker** | 6–8h | **~10%** | Medium (A/B vs checklist needed) | None |
| 6 | **UCCI calibrated cascade on 5-criterion judge + CoCoA** | 6–8h | **~3% (~31% judge-cost cut)** | Low | None |
| 7 | **Adaptive halting head for CoRefine (ML the Bundle 7 threshold)** | 1–2 days | **5–10% on refining chapters** | Low (compliance: borderline — logistic regression inference in-cluster) | None |
| 8 | **LangGraph × NVIDIA Speculative Execution on mgsr→sawc loopback** | 1 day | **5–10%** | Low (LangGraph version pinning) | None |
| 9 | **KD_STUDY_SEM=1 → 2 (parallel chapters, bounded harmonize)** | 1–2h + validation | **2× study-level throughput** (latency to first chapter unchanged thanks to Bundle 6 streaming) | Medium (validate `book_harmonize` cache behavior) | None |
| ~~10~~ | ~~LLMLingua-2 prompt compression~~ | — | **DROPPED** | Violates "no in-cluster inference" rule | — |

**Combined ceiling for a single chapter:** ~3–5× faster (≈30 min → ≈8–12 min for fast-path chapters, ≈15–20 min for refining chapters).

**Recommended ship sequence:** 1 + 3 first (parallel, both config-level), then 2 (the qualitative leap), then 9 (throughput), then 4. Items 5–8 after the dust settles.

## 2. Current Synth wall-time map (empirical from code map)

Per-chapter, single-pass, no CoRefine looping:

| Node | Wall-time | % of chapter | Why slow | LLM call count |
|---|---|---|---|---|
| **sawc_write** | 15–25 min | **~60%** | Stage-sequential DAG; N=2 drafts/section × 10–15 sections + serial critic per section | ~30–45 (15 sec × (2 drafts + 1 critic)) |
| **digest_construct** | 7–9 min | ~25% | 16-concurrent per-source LLM; 50–250 sources | 50–250 |
| **checklist_eval** | 3–8 min | ~10% | Batched judge + CoCoA 2-stage + 8 faithfulness claim judges | ~12 |
| **outline_sdp** | 2–4 min | ~5% | N=3 candidates + vote | 4 |
| **sawc_derive** | 2–5 min | sparse | Only thin subtopics (~3–8 per chapter) | ~10–25 |
| mgsr_replan | <100ms / 30–50s | trivial | Fast-path when score≥0.80 | 0–1 |
| render_audit_write | <500ms | trivial | Zero LLM | 0 |

**CoRefine loop multiplier:** up to 5× on sawc_write in worst case, mitigated by:
- Bundle 7 iter-1 short-circuit (`score<0.5 at iter≤1 → halt`)
- OP-12 best-seen rescue (ship highest-scoring iteration even on plateau/budget halt)

**Cross-chapter:** `book_harmonize` adds ~5–15 min total amortized after all chapters complete.

**Bottleneck conclusion:** SAWC dominates (~60%) — any plan that doesn't attack SAWC is fiddling at the edges.

## 3. Constraints (NON-NEGOTIABLE)

1. **Free-tier hosted APIs only.** Providers in use: NVIDIA NIM, Mistral, Gemini, Groq, Cerebras, SambaNova, DeepSeek. No paid tiers, no Together/OpenRouter/Anthropic.
2. **NO inference inside COELHO Cloud cluster.** Per `project_local_vs_rotator_architecture` — single-node K8s, CPU-bursty inference threatens platform stability. Existing CPU operations (HDBSCAN, c-TF-IDF, embedding chunking, Borda aggregation, Rust tokenizers) are grandfathered; no NEW local model inference (no LLMLingua-2 BERT, no local rerankers, no local distilled judges).
3. **Quality must NOT degrade.** `feedback_kd_quality_over_speed` — tokens are free, runtime is the concern, but accuracy is the constraint.
4. **Use the existing rotator surface.** No new rotator instances; per the architectural rule, ship #2 enriches the arm pools of the existing `dd-grader`, `dd-synth-write`, `dd-corpus-normalize` rotator names rather than creating new ones.

## 4. The 10 ranked ships (detailed)

### 🥇 #1 — Constrained decoding / structured outputs

**Where:** `apps/fastapi/domains/dd/synth/sawc/node.py` writer + critic calls, `apps/fastapi/domains/dd/synth/checklist/node.py` judge call, every other site that builds a Pydantic-validated response from an LLM.

**The problem:** Every node that expects a Pydantic-shaped response retries on JSON parse failure or schema mismatch. Empirical retry rate ~27% across nodes; each retry is a full LLM round-trip.

**The fix:** Use provider-native structured output mode (`response_format={"type": "json_schema", ...}`) on arms that support it:
- Gemini 2.5 Flash / Pro — `response_schema=` with Pydantic
- NIM — Pydantic mode on Nemotron-3 endpoints
- Mistral — `response_format` since 2025-11
- Groq — `response_format` since 2025-10

**Empirical:** Cuts retry rate ~92% (27% → 2%) on structured-response tasks (paper benchmarks; consistent across providers).

**Speedup:** ~10–25% on SAWC, less on checklist (already 1 batched call but the CoCoA repair cycle benefits).

**Compliance:** ✅ Per-call flag, no local inference, no new rotator.

**Risk:** Very low. If an arm doesn't support structured output, fall back to current free-form + parse path.

**LOC:** ~50 across the 4 call sites (writer / critic / judge / cocoa).

### 🥈 #2 — Heterogeneous role-routing via arm-pool enrichment

**Where:** Rotator arm registration config (presumably `apps/fastapi/domains/llm/rotator/...`).

**The problem:** Today every rotator (`dd-grader`, `dd-synth-write`, `dd-corpus-normalize`) has a relatively homogeneous arm pool. The bandit can't pick the right model for the workload because the workload-fit arms aren't in the pool.

**The fix:** Add to each existing rotator's arm pool — no new rotator instances:

| Provider/Model | Best fit for | TTFT | Throughput | Free-tier limit |
|---|---|---|---|---|
| **Cerebras Llama-3.3-70B** | SAWC writer drafts (short prompt, fast decode) | <200ms | 1,800–2,600 tok/s | 1M tok/day, 30 RPM, 8K ctx |
| **Groq Llama-3.3-70B** | Critic / judge (sub-100ms TTFT for picker calls) | <100ms | 300–493 tok/s | Generous free tier |
| **Gemini 2.5 Flash** | Digest extraction (1M ctx → kills chunking) | 0.62s | 224 tok/s | 1,500 RPM free |
| **NIM Nemotron-3-MTP** | Long-context CoCoA + general-purpose | varies | 2–6× via MTP | NIM free tier |
| **SambaNova Llama-3.3-70B** | Backup writer | ~150ms | 460+ tok/s | Free tier |
| **DeepSeek-V3.1** | Backup judge | ~300ms | varies | Free via aggregators |

The FGTS-VA bandit's variance-awareness (`σ²` re-classification, see `project_planner_langchain_validation_2026_05_23`) will discover the workload-fit arm per rotator automatically — same mechanism that re-classified `qwen3.5-397b` 0.18→0.016 on the LangChain corpus.

**Speedup:** 2–3× per-chapter via parallel pipelining across providers (independent rate limits multiply free capacity ~4×).

**Compliance:** ✅ All providers are free-tier and already in use.

**Risk:** Low. Existing FGTS-VA + kill switches (`KD_DISABLE_FGTS_VA → ts`, `KD_DISABLE_BANDIT_TS → ucb`) cover regression.

**LOC:** ~30 (arm registry config). No code path changes.

### 🥉 #3 — NIM EAGLE-3 / Nemotron-3-MTP arms

**Where:** Same rotator arm registry as #2.

**The problem:** Standard NIM Llama-3.3-70B doesn't use speculative decoding. Nemotron-3 endpoints with MTP (multi-token prediction) speed up generation 2–6× at zero quality loss.

**The fix:** Register Nemotron-3-MTP-capable model IDs as new arms. Verify per-endpoint which IDs actually expose MTP (some endpoints publish MTP only for self-hosted deployments — confirm at registration time).

**Empirical:** EAGLE-3 tree-attention drafter 2–6× (arxiv 2503.01840). Nemotron-Labs-Diffusion 5.99× TPF on 8B (NVIDIA blog 2026-05-20). P-EAGLE adds 1.69× on B200.

**Speedup:** 2–6× on any node that lands on these arms (FGTS-VA will converge to them when they win on `latency_per_token × pass_rate`).

**Compliance:** ✅ NIM free tier, no local inference.

**Risk:** Very low.

**LOC:** ~20 (registry).

### #4 — Optimal-Stopping Best-of-N

**Where:** `apps/fastapi/domains/dd/synth/sawc/node.py` around `asyncio.gather([writer_call for _ in range(_N_DRAFTS)])`.

**The fix:** Instead of always firing `_N_DRAFTS=2` writer calls, fire 1 → score → decide whether the second is worth firing (sequential decision rule per arxiv 2510.01394, Oct 2025).

**Empirical:** 15–35% sample reduction at equal Best-of-N quality.

**Speedup:** 10–20% on SAWC (~50% of chapter time × 15–35% of writer calls).

**Compliance:** ✅ Pure algorithm change.

**Risk:** Low. Validate against checklist pass rate on FastMCP + LangChain corpora.

**LOC:** ~50.

### #5 — CISC (Confidence-Informed Self-Consistency) replacing MAMM critic-picker

**Where:** `apps/fastapi/domains/dd/synth/sawc/service.py` MAMM-Refine critic prompt.

**The fix:** Writer emits a self-score with each draft (`{draft, confidence}`). Weighted majority/argmax over self-scores replaces the separate critic LLM call per section.

**Empirical:** 40–50% sample-count reduction at equal quality (arxiv 2502.06233).

**Speedup:** Saves 1 critic round-trip per section × 10–15 sections per chapter ≈ ~10% per chapter.

**Compliance:** ✅ Same rotator call shape, just extra output field.

**Risk:** Medium. Replaces a quality-protecting gate. **Requires A/B vs current MAMM critic** on a held-out corpus before defaulting; ship behind `KD_SAWC_CISC_PICKER=true` flag with shadow mode.

**LOC:** ~80.

### #6 — UCCI calibrated cascade on judges

**Where:** `apps/fastapi/domains/dd/synth/checklist/service.py` 5-criterion judge + `checklist/cocoa.py` two-stage CoCoA cascade.

**The fix:** Isotonic-regression-calibrated escalation from cheap to expensive judge model. Stop on confidence (arxiv 2605.18796).

**Empirical:** 31% judge-cost cut at micro-F1=0.91 (75K-query NER workload).

**Speedup:** ~3% per-chapter (judge is ~10% of chapter wall-time × 31% cut).

**Compliance:** ✅ Isotonic regression is fit offline; runtime is lookup table — no local inference.

**Risk:** Low.

**LOC:** ~120 (cascade orchestrator + calibration fit script).

### #7 — Adaptive halting head for CoRefine

**Where:** `apps/fastapi/domains/dd/synth/graph.py::_route_after_mgsr`.

**The problem:** Bundle 7's hardcoded `score<0.5 at iter≤1 → halt` catches obvious failures but misses borderline cases (0.5–0.7) where iter-2 won't recover either.

**The fix:** Train a logistic regression classifier on `(chapter_features, iter1_checklist_pass) → P(iter≥2 recovers to ≥0.80)`. Features: section count, avg section length, identifier overlap, citation density, prev-iter delta. ~20–30 features.

**Empirical:** LoopUS adaptive halting (arxiv 2605.11011v1); 5–10% per-chapter on chapters that would have looped.

**Compliance:** ⚠️ **BORDERLINE.** Logistic regression is technically inference (dot product + sigmoid, ~µs CPU). Functionally same class as the c-TF-IDF / Borda aggregation already running in-cluster. **User decision required.**

**Risk:** Low. Worst case the prediction is ignored and current threshold applies.

**LOC:** ~150 (training script + runtime predict + 30 features). Plus ~50–100 prior chapter runs as training data.

### #8 — LangGraph × NVIDIA Speculative Execution

**Where:** `apps/fastapi/domains/dd/synth/graph.py` conditional edge.

**The fix:** Use LangChain × NVIDIA Speculative Execution (announced 2026-03-16) to fire `mgsr_replan` AND start `sawc_write` in parallel; discard the sawc result on HALT.

**Speedup:** 5–10% per chapter (eliminates the mgsr → sawc serial latency gap).

**Compliance:** ✅ Pure orchestration, no inference.

**Risk:** Low — but depends on stable LangGraph version pinning + LangChain NVIDIA integration version.

**LOC:** ~40 (compile-time annotation).

### #9 — KD_STUDY_SEM=1 → 2

**Where:** `apps/fastapi/domains/dd/synth/dispatch.py:_STUDY_SEM`.

**The fix:** Lift the per-study chapter semaphore from 1 to 2 (single-node K8s; chapters are API-bound not CPU-bound).

**Speedup:** 2× study-level throughput. Per-chapter latency unchanged for first chapter (Bundle 6 streaming already delivers chapter 1 ASAP).

**Compliance:** ✅ Concurrency knob.

**Risk:** Medium. Validate:
- `book_harmonize` cache doesn't regress with 2 chapters writing concurrently (it runs AFTER all chapters anyway — should be safe).
- Rotator rate limits hold under doubled load. FGTS-VA will reject arms hitting limits, but heavily-used arms might saturate. Mitigated by the wider arm pool from #2.

**LOC:** ~10.

### ~~#10 — LLMLingua-2~~ (DROPPED)

**Reason:** Requires running a distilled BERT classifier in-cluster for token scoring. Violates `project_local_vs_rotator_architecture`. The benefit (3–6× digest token reduction) is captured anyway by ship #2 routing digest to Gemini 2.5 Flash 1M-ctx — no chunking, no compression needed.

## 5. Compliance audit

| Ship | Free-tier API only | No in-cluster inference | Existing rotator | Quality-protected |
|---|---|---|---|---|
| 1 Constrained decoding | ✓ | ✓ | ✓ | ✓ (fallback on unsupported arms) |
| 2 Role-routing arms | ✓ | ✓ | ✓ | ✓ (FGTS-VA convergence) |
| 3 EAGLE-3 / MTP arms | ✓ | ✓ | ✓ | ✓ |
| 4 Optimal-Stopping BoN | ✓ | ✓ | ✓ | ⚠ A/B needed |
| 5 CISC critic-replace | ✓ | ✓ | ✓ | ⚠ A/B + shadow flag |
| 6 UCCI cascade | ✓ | ✓ | ✓ | ✓ |
| 7 Adaptive halting | ✓ | ⚠ logistic regression in-cluster | ✓ | ✓ |
| 8 Spec execution | ✓ | ✓ | ✓ | ✓ |
| 9 KD_STUDY_SEM=2 | ✓ | ✓ | ✓ | ⚠ validate harmonize |
| ~~10 LLMLingua-2~~ | ~~✓~~ | ❌ | — | — (dropped) |

## 6. Recommended ship sequence

Targeted at **maximum per-chapter speedup with zero quality risk in the first wave**:

**Wave 1 (this week, ~2 days total, no quality risk):**
1. **#1 Constrained decoding** (3–4h) — start here. Independent. ~10–25% wall-time win, kills retries everywhere.
2. **#3 EAGLE-3 / MTP arm registration** (2–4h, parallel with #1) — config-only. Big multiplier on every node hitting these arms.

**Wave 2 (next week, ~2 days, biggest qualitative leap):**
3. **#2 Role-routing arm-pool enrichment** (1 day) — depends on #1 + #3 being live so FGTS-VA can observe the new arms cleanly. The 2–3× per-chapter leap.
4. **#9 KD_STUDY_SEM=1 → 2** (1–2h + validation) — once #2 widens the rotator pool, this is safe. 2× study throughput.

**Wave 3 (week 3, A/B-gated):**
5. **#4 Optimal-Stopping BoN** (4–6h, shipped behind `KD_SAWC_OPTIMAL_STOPPING=true`) — A/B against current N=2 fixed.
6. **#8 LangGraph Speculative Execution** (1 day) — LangGraph version pinning + integration.

**Wave 4 (later, after the stack stabilizes):**
7. **#6 UCCI cascade** — small absolute win but cleanest engineering.
8. **#5 CISC critic-replace** — needs ≥50 prior chapter runs for A/B validation.
9. **#7 Adaptive halting** — needs ≥50 prior chapter runs for training data. Borderline compliance: ship only after user explicit approval.

## 7. Out of scope (deliberately deferred)

| Item | Reason |
|---|---|
| LLMLingua-2 / LongLLMLingua | Violates no-in-cluster-inference rule. Coverage via #2 (Gemini Flash 1M-ctx). |
| ThinkPRM replacing checklist | Quality risk on the SAWC code-accuracy gate — needs >100 chapter runs of validation. |
| Local cross-encoder rerankers | No-in-cluster-inference rule. NIM-hosted `nemotron-rerank-1b-v2` already covers this need. |
| Local quantized LLMs / vLLM | No-in-cluster-inference rule. |
| Paid-tier providers (Together, OpenRouter, Anthropic) | Free-tier-only constraint. |
| Pivotal Token Search confidence gates | Requires custom logit access — not available on free-tier hosted endpoints. |
| Lookahead Decoding / Jacobi CLLMs | Provider-side feature; depends on which NIM endpoints expose it. Track as #3 follow-up. |
| Bootstrapped "Majority of the Bests" | Marginal vs Optimal-Stopping BoN; pilot AFTER #4 measures. |

## 8. References

- EAGLE-3 paper: https://arxiv.org/html/2503.01840v1
- Nemotron-3 / NIM speculative decode: NVIDIA blog 2026-05-01, 2026-05-20
- Cerebras free tier + benchmarks: cerebras.ai/blog/cerebras-cs-3-vs-groq-lpu, inference-docs.cerebras.ai
- Groq 2026 latencies: voiceflow.com/blog/groq
- Gemini 2.5 Flash latency analysis: artificialanalysis.ai/models/gemini-2-5-flash
- Constrained decoding 2026: collinwilkins.com/articles/structured-output
- Optimal-Stopping vs Best-of-N: arXiv 2510.01394 (Oct 2025)
- CISC (Confidence-Informed Self-Consistency): arXiv 2502.06233
- UCCI calibrated cascade: arXiv 2605.18796 (May 2026)
- LoopUS adaptive halting: arXiv 2605.11011v1
- Anchor-Refinement halting: arXiv 2603.15051
- ThinkPRM: arXiv 2504.16828
- MAMM-Refine (current critic baseline): arXiv 2503.15272
- LangChain × NVIDIA Speculative Execution: blog.langchain.com/nvidia-enterprise/ (2026-03-16)
- RefineBench (Bundle 7 grounding): Nov 2025 paper
- FGTS-VA bandit: NeurIPS 2025 (project_rotator_bandit_sota_2026_05_23)
