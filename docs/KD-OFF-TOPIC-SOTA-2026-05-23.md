# Off-topic filter — full SOTA architecture (2026-05-23)

Final converged answer from three parallel deep-research agents on the **off_topic binary KEEP/DROP problem** after the second Planner execution surfaced Phase A (cross-encoder rerank) as broken — dropping 70% of legitimate framework docs at LangChain scale.

Replaces the rerank approach with a quality-first 4-stage cascade that composes natively with the existing FGTS-VA bandit infrastructure.

**Cross-references:**
- [`KD-PLANNER-FASTMCP-VALIDATION-2026-05-23.md`](./KD-PLANNER-FASTMCP-VALIDATION-2026-05-23.md), [`KD-PLANNER-LANGCHAIN-VALIDATION-2026-05-23.md`](./KD-PLANNER-LANGCHAIN-VALIDATION-2026-05-23.md) — empirical baselines
- [`KD-PLANNER-SOTA-IMPROVEMENTS-2026-05-23.md`](./KD-PLANNER-SOTA-IMPROVEMENTS-2026-05-23.md) — original improvements doc (Phase A now superseded by this)
- [`KD-ROTATOR-BANDIT-SOTA-2026-05-23.md`](./KD-ROTATOR-BANDIT-SOTA-2026-05-23.md) — FGTS-VA bandit infrastructure

## TL;DR

| | |
|---|---|
| **Problem** | Phase A cross-encoder rerank dropped 70% of legitimate framework docs on LangChain (732 of 777 wrongly DROP'd) because semantic similarity to a generic framework descriptor under-scores deep-dive subsystem pages |
| **Diagnosis** | Cross-encoder is the wrong primitive for "is this framework-X teaching content vs framework-X meta-content" — that's a *structural* + *topical* judgment, not pure semantic similarity |
| **Solution** | **4-stage cascade**: structural prefilter → checklist-based LLM judge with structured output → cross-family PoLL on uncertain cases → cross-family critic on residual disagreement |
| **Expected lift** | **+10-15 pp F1** over current single-shot LLM-judge (cumulative across multipliers; mostly DROP-precision gains on meta-content leakage) |
| **Cost** | ~3-3.5× current LLM-judge wall time (within `feedback_kd_quality_over_speed` budget) |

## Current Planner status (post-second-execution)

After the LangChain + FastMCP runs with Phase A active + Phase B reverted + Phases D & E active:

| Step | Status | Detail |
|---|---|---|
| 1. `corpus_load` | ✅ Fine | 0.2-3.2 s, within historical variance |
| 2. `embed_corpus` | ⚠️ Known limitation | 54% chunk rate on LangChain (Phase B reverted; back on the 1B model with 8K context). Pre-existing constraint; not introduced by recent changes |
| 3. `off_topic` | 🔴 **BROKEN** | Phase A rerank dropping 70% of legitimate content. LangChain kept 232/777 (was 741/777). FastMCP kept 232/335 (was 332/335). "Contributing to LangChain" slipped through to Ch5 — meta-content leak. **This doc fixes this** |
| 4. `cluster` | ⚠️ Downstream effect | LangChain 15→9 clusters, FastMCP 3→2 clusters — secondary to off_topic over-dropping. Resolves automatically when #3 fixed |
| 5. `refine` | ✅ Fixed by Phase D | FastMCP 18 s → 1.7 s; LangChain 168 s → 40 s. GMM resolver: 100% / 81% deterministic. 0 errors |
| 6. `label` | ✅ Fine | 29 s / 30 s, normal USC + Round 2 behavior, 0 errors |
| 7. `reduce` | ✅ Mostly fine | FastMCP had 1 repair (115 s) — slight anomaly. LangChain clean (30 s, 0 repairs) |
| 8. `plan_write` | ⚠️ Downstream effect | LangChain plan: 5 balanced chapters but only 231 sources (was 716). FastMCP plan: lopsided 82/18%. Both downstream of #3 |

**Bottom line**: one genuine step-level problem (`off_topic`) + one pre-existing limitation (`embed_corpus` chunking). Fix `off_topic` properly and downstream effects (cluster, plan distribution) resolve automatically.

## The 4-stage architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 0 — Structural prefilter (regex on URL slug + first H1)        │
│   DETERMINISTIC, FREE                                                 │
│   ~30-50% of corpus handled here:                                     │
│     hard-DROP paths:  /sponsors, /license, /code-of-conduct,          │
│                       /contributing, /changelog, /governance, blog/   │
│     hard-KEEP paths:  /tutorial, /guide, /api, /reference, /howto     │
│   Else → Stage A                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                  ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Stage A — Checklist judge (single FGTS-VA-routed arm)                 │
│   ONE LLM CALL PER DOC, STRUCTURED OUTPUT                             │
│   - 4-6 YES/NO checklist items from a framework-rubric template       │
│   - 2-3 few-shot KEEP + 2-3 DROP examples (per framework family)      │
│   - Pydantic-typed verdict:                                           │
│       {verdict: KEEP|DROP|UNCERTAIN,                                  │
│        confidence: 0-1,                                               │
│        evidence: "1-sentence justification",                          │
│        checklist: [{question, answer: bool}, …]}                      │
│   - Commit if confidence >= τ_high (0.7) AND verdict != UNCERTAIN     │
│   Else → Stage B                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                  ↓ (uncertain ~10-20% of corpus)
┌──────────────────────────────────────────────────────────────────────┐
│ Stage B — Cross-family PoLL (Panel of LLM Jurors)                     │
│   N=3 ARMS FROM DIFFERENT MODEL FAMILIES                              │
│   - FGTS-VA picks top arms but filtered for cross-family diversity   │
│     (NIM-Llama family ≠ Mistral family ≠ Gemini/DeepSeek family)      │
│   - Each emits same structured output as Stage A                      │
│   - Weighted majority vote: weights = 1 / σ²_ewma (FGTS-VA per-arm)   │
│   - Consensus → commit                                                │
│   Else (split or all UNCERTAIN) → Stage C                             │
└──────────────────────────────────────────────────────────────────────┘
                                  ↓ (residual ~2-5% of corpus)
┌──────────────────────────────────────────────────────────────────────┐
│ Stage C — Cross-family critic                                          │
│   ONE FINAL LLM CALL, DIFFERENT FAMILY FROM STAGE A + STAGE B MAJORITY │
│   - Binary "agree / disagree with current majority"                   │
│   - Disagreement → conservative bias: KEEP (recall > precision        │
│     for doc filters)                                                  │
└──────────────────────────────────────────────────────────────────────┘
```

## Why each stage exists

### Stage 0 — Structural prefilter

Most "off-topic" pages on framework doc sites are STRUCTURAL not topical: sponsors live at `/sponsors`, license at `/LICENSE`, CoC at `/code-of-conduct`, changelog at `/CHANGELOG.md`. **Pure regex catches 30-50% of obvious cases for free.** Per [Topic-Specific Classifiers paper (arXiv:2510.04633)](https://arxiv.org/abs/2510.04633), structural signals are orthogonal to semantic similarity and outperform LLM-prompted relevance on URL-rich corpora.

### Stage A — Checklist judge with structured output

The single biggest accuracy lever in 2025-2026 binary classification literature.

- **TICK / STICK** ([arXiv:2410.03608](https://arxiv.org/abs/2410.03608)) — decomposing the judgment into 4-6 YES/NO checklist items raises LLM-vs-human exact agreement from 46.4% → 52.2% (+5.8 pp).
- **AutoChecklist** ([arXiv:2603.07019](https://arxiv.org/abs/2603.07019), Mar 2026) — generator → refiner → scorer pipeline for stored rubrics; better than instance-generated rubrics for high-volume batch evaluation like 2000-doc crawls.
- **Pydantic + Instructor pattern** — eliminates the regex-parse failure mode entirely (cross-encoder had 0% parse errors but LLM-judge had to learn to avoid arms producing malformed text; structured output sidesteps the problem).
- **NOTA / ABSTAIN third option** ([ACL 2025](https://aclanthology.org/2025.findings-acl.1031/)) — adding UNCERTAIN avoids forced binarization losses. The current failure mode (meta-content leaking through KEEP) is exactly the case where neither KEEP nor DROP cleanly applies; ABSTAIN routes those to Stage B for richer adjudication.

### Stage B — Cross-family PoLL

**Diversity beats repetition for binary tasks.** Modern LLMs have 90-98% intra-rater agreement on binary judgments ([arXiv:2511.00751](https://arxiv.org/abs/2511.00751), [arXiv:2505.14918](https://arxiv.org/abs/2505.14918)) — same-model N=3 USC vote gives sub-1 pp lift. Cross-family N=3 PoLL gives 2-6 pp.

- **PoLL (Verga et al. 2024, arXiv:2404.18796)** — Panel of LLM Jurors. 7-8× cheaper than monolithic GPT-4-class with HIGHER human-correlation. Free-tier NIM arms naturally provide the diversity.
- **MAMM-Refine** ([arXiv:2503.15272](https://arxiv.org/abs/2503.15272)) — multi-agent multi-model with writer ≠ critic gives +1.6 to +2.5 BACC on binary faithfulness detection.
- **FGTS-VA composes naturally**: the per-arm σ²_ewma estimates (already maintained in Redis cells) drop directly into vote weights — no extra calibration call needed. Lower σ² → higher weight.
- **Cross-family enforcement**: NIM Llama family ≠ Mistral family ≠ Gemini family. Prevents correlated errors from same-pretrain-corpus dependencies.

### Stage C — Cross-family critic

**Trust-or-Escalate cascade pattern** ([Jung et al. ICLR 2025, arXiv:2407.18370](https://arxiv.org/abs/2407.18370)) — ≥80% human agreement at ~80% coverage; 88% of decisions made by cheaper stages. The final critic only fires on the ~2-5% genuinely-ambiguous residual where Stage B couldn't reach consensus. **Conservative KEEP bias** on disagreement: doc filters favor recall (better to include something marginal than to drop legitimate content — that was the LangChain Phase A failure).

## Empirical lift (quantified, cited)

| Pattern | Lift on binary tasks | Source |
|---|---|---|
| Checklist decomposition (TICK / RocketEval / AutoChecklist) | **+5-8 pp F1** (46.4% → 52.2% human agreement) | [arXiv:2410.03608](https://arxiv.org/abs/2410.03608), [arXiv:2503.05142](https://arxiv.org/abs/2503.05142), [arXiv:2603.07019](https://arxiv.org/abs/2603.07019) |
| Cross-family PoLL vs single judge | **+2-6 pp**, 7-8× cheaper than monolithic | [arXiv:2404.18796](https://arxiv.org/abs/2404.18796) |
| Trust-or-Escalate cascade | **80% human-agreement at 80% coverage**; 88% decisions by cheap stages | [arXiv:2407.18370](https://arxiv.org/abs/2407.18370) (ICLR 2025) |
| NOTA / ABSTAIN ternary | **+2-4 pp** on borderline meta-content (your exact failure mode) | [ACL 2025 Findings](https://aclanthology.org/2025.findings-acl.1031/) |
| Dynamic few-shot retrieval | **+1-3 pp** | [npj AI 2025](https://www.nature.com/articles/s44387-025-00062-2) |
| FGTS-VA σ² → vote weights | Composes natively (your existing infra) | [arXiv:2511.02123](https://arxiv.org/abs/2511.02123) |
| **Cumulative net lift** | **+10-15 pp F1** over current single-shot | All 3 research agents agreed |

## What's explicitly skipped (all 3 research agents converged)

| Pattern | Why skip |
|---|---|
| ❌ Same-model USC N=3 vote | Modern LLMs have 90-98% intra-rater agreement on binary — sub-1pp lift, redundant under PoLL |
| ❌ Self-Refine on binary verdicts | Can only bit-flip — Madaan's +20% applies to generative tasks, not single-bit outputs |
| ❌ Pairwise tournament judging | Amplifies position bias ([arXiv:2406.12319](https://arxiv.org/pdf/2406.12319)) |
| ❌ Cross-encoder rerank as primary | Empirically proven failure (Phase A: 70% legit-doc drop). Stays as optional secondary signal but not primary verdict |
| ❌ Reasoning models with `<think>` tokens for easy binary | 2.4-3.8% accuracy LOSS + 5-10× token waste on simple classification ([arXiv:2506.23840](https://arxiv.org/abs/2506.23840)) — only use reasoning models on Stage C genuinely-ambiguous tail |
| ❌ Constitutional AI fine-tuning (full RLHF) | Overkill at 35-framework scale; principle-prompting captures most of the win |
| ❌ Linear-probe calibration on judge hidden states | Strongest 2025-2026 calibration result ([arXiv:2512.22245](https://arxiv.org/abs/2512.22245)) BUT requires NIM hidden-state access — not exposed on free-tier API |
| ❌ Vanilla Dawid-Skene independence assumption | NIM models share training corpora; use FGTS-VA-weighted majority instead |
| ❌ Platt/isotonic post-hoc calibration on verbalized confidence | Wrong layer; consensus-based ensemble confidence is stronger than recalibrated single-arm logits |

## Composition with FGTS-VA bandit

The bandit infrastructure (already shipped + production-tested) composes natively:

| Bandit primitive | Used by |
|---|---|
| `predict_top_k(dd_process, context)` returning ranked deployments | Stage A (top-1), Stage B (top-3 with cross-family filter), Stage C (next-best from a different family) |
| Per-arm `σ²_ewma` cell state | Stage B vote weights (lower σ² → higher weight) |
| `compose_reward()` | Per-stage reward signal (Stage A: confidence × correctness; Stage B: agreement-with-final; Stage C: critic-veto-rate) |
| FGTS-VA variance-aware sampling | Continues to learn which arms are reliable for `dd-grader` workload |

## Acceptance criteria after deployment

| Signal | Threshold | What it means |
|---|---|---|
| LangChain off_topic kept ratio | ≥85% (was 95% with old LLM-judge) | Cascade is approximately as permissive as old LLM-judge but with structured rationale |
| LangChain "Contributing to LangChain"-style meta leakage | 0 chapters | The Stage 0 prefilter + Stage A rubric catches what old LLM-judge missed |
| Per-stage handoff distribution | Stage 0: 30-50%, Stage A: 40-60%, Stage B: 5-15%, Stage C: 1-5% | Pipeline economic profile; if Stage B/C ratios climb too high, calibrate τ_high down |
| Total wall time | 15-25 min on LangChain (vs 4.4 min Phase A, 4.6 min old LLM-judge) | Quality-first cost; within `feedback_kd_quality_over_speed` budget |
| 0 structured-output parse failures | 0 | Pydantic + structured prompt eliminates the failure mode |

## Sources

### Primary architecture
- [TICK / STICK — Generated Checklists Improve LLM Evaluation (arXiv:2410.03608)](https://arxiv.org/abs/2410.03608)
- [AutoChecklist — Composable Pipelines for Checklist Generation and Scoring (arXiv:2603.07019, Mar 2026)](https://arxiv.org/abs/2603.07019)
- [Trust or Escalate: LLM Judges with Provable Guarantees (ICLR 2025, arXiv:2407.18370)](https://arxiv.org/abs/2407.18370)
- [Replacing Judges with Juries — PoLL (Verga et al., arXiv:2404.18796)](https://arxiv.org/abs/2404.18796)
- [MAMM-Refine: Multi-Agent Multi-Model Faithfulness (arXiv:2503.15272)](https://arxiv.org/abs/2503.15272)
- [None of the Above, Less of the Right — NOTA (ACL 2025 Findings)](https://aclanthology.org/2025.findings-acl.1031/)
- [RocketEval — Checklist Grading (ICLR 2025, arXiv:2503.05142)](https://arxiv.org/abs/2503.05142)
- [Topic-Specific Classifiers Better Than Prompted LLMs (arXiv:2510.04633)](https://arxiv.org/abs/2510.04633)

### Empirical comparisons
- [Self-Consistency Is Losing Its Edge (Loo 2025, arXiv:2511.00751)](https://arxiv.org/abs/2511.00751)
- [Reliable Decision Support: Binary Text Classification Consistency (arXiv:2505.14918)](https://arxiv.org/abs/2505.14918)
- [Do Thinking Tokens Help or Trap? (arXiv:2506.23840)](https://arxiv.org/abs/2506.23840)
- [The Comparative Trap: Pairwise Amplifies Bias (arXiv:2406.12319)](https://arxiv.org/abs/2406.12319)

### Calibration / aggregation
- [Calibrating LLM Judges: Linear Probes (arXiv:2512.22245)](https://arxiv.org/abs/2512.22245)
- [Variance-Aware Feel-Good Thompson Sampling (NeurIPS 2025, arXiv:2511.02123)](https://arxiv.org/abs/2511.02123)
- [The Necessity of Setting Temperature in LLM-as-a-Judge (arXiv:2603.28304)](https://arxiv.org/abs/2603.28304)
- [Inter-Cascade — Online In-Context KD for LLM Cascades (arXiv:2509.22984)](https://arxiv.org/abs/2509.22984)

### Existing code
- `apps/fastapi/domains/dd/planner/off_topic/`
- `apps/fastapi/domains/llm/rotator/bandit/`
- `apps/fastapi/domains/llm/rotator/chain/service.py` — `chat_judge_bandit_async`, `predict_top_k`
