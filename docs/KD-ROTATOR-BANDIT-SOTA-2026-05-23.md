# KD Rotator — Bandit Algorithm SOTA Decision (2026-05-23)

Successor decision doc to [`KD-ROTATOR-PARETO-BANDIT-DECISION-MAY2026.md`](./KD-ROTATOR-PARETO-BANDIT-DECISION-MAY2026.md). Revisits the bandit family in light of May 2026 SOTA literature and **scopes the next round of changes to bandit-core only**, deferring operational phases until LangFuse + OpenTelemetry instrumentation gives us signal to act on.

## Current state (baseline)

`apps/fastapi/domains/llm/rotator/bandit/`:
- **LinUCB** with geometric forgetting (γ=0.01) on sufficient statistics
- 24-dim context vector (bias, request features, temporal sin/cos, per-provider error rates)
- Per-(deployment, dd_process) cell state in Redis (~2 KB/cell)
- Warm-start from benchmark composite scores
- Top-K cascade for failure fallback
- Provisional reservations (thundering-herd protection)
- Provider-level slot reservations (NIM=2 concurrent)
- ADWIN drift detection per cell with cell-reset on alarm
- Per-error-class reward penalties (`auth_error=-0.80`, `timeout=-0.60`, `rate_limit=-0.10`, etc.)

Status: **working in production**, shipped 2026-05-14 behind `KD_USE_PARETO_BANDIT` flag, shadow-validated.

## Naming note

The current module is named after [ParetoBandit (arXiv:2604.00136, Taberner-Miller, Mar 2026)](https://arxiv.org/abs/2604.00136). Per the paper itself, the algorithm is a **constrained single-objective LinUCB with budget pacing + geometric forgetting** — *not* a multi-objective Pareto-front bandit. The name is aspirational. For free-tier rotation (cost ≈ 0 across all arms), the budget-pacing component is inert; what's actually doing work is the LinUCB + forgetting + ADWIN stack.

## Decision

Ship the bandit-core path **only**:

| Phase | Change | LOC | Why |
|---|---|---|---|
| **3a** | LinUCB → **LinTS** (Linear Thompson Sampling) | ~30 | Posterior sampling converges faster than UCB's deterministic upper bound on sample-poor problems; well-established since [Chapelle & Li, NIPS 2011](https://papers.nips.cc/paper/2011/hash/e53a0a2978c28872a4505bdb51db06dc-Abstract.html). Same `A_a`, `b_a` state — drop-in. |
| **3c** | LinTS → **FGTS-VA** (Variance-Aware Feel-Good Thompson Sampling) | ~60 | Tighter regret `Õ(√(d·log\|F\|·Σσ²) + d)` when reward variance differs across arms — true in free-tier rotation (rate-limited providers are intrinsically noisier than direct-API providers). [NeurIPS 2025, arXiv:2511.02123](https://arxiv.org/abs/2511.02123). |

**Total: ~90 LOC of bandit changes.** Same Redis state shape, same warm-start path, same forgetting, same ADWIN. Ship each behind its own feature flag (`KD_USE_LINTS=1`, `KD_USE_FGTS_VA=1`); shadow-validate against the existing `dd.pareto_shadow_agreement` metric before flipping.

## Why stop here for now

LinUCB → LinTS → FGTS-VA captures the **academic SOTA for the bandit core itself** given our constraints (sample-poor, non-stationary, cheap state, warm-start from benchmark). Rough estimate: **70-80% of total achievable improvement at ~5% of the full-list LOC** (the full list ran to ~740 LOC across 9 phases).

LinUCB is working today. The remaining phases (3b, 4a, 4b, etc. — see deferred list below) don't make the **bandit** better — they fix **operational** issues the bandit can't see. Without LangFuse + OTel data telling us which operational failure mode dominates, adding them is speculation.

## Why we skipped the alternatives

| Family | Verdict |
|---|---|
| **MOL-TS** (Multi-Objective Linear TS, NeurIPS 2025) | Doesn't apply — free-tier means no cost-quality Pareto trade-off. Our reward is lexicographic ("best first, fall back on failure"), not Pareto. |
| **BaRP** (preference-vector routing, arXiv:2510.07429) | Future-state if we ever expose per-request preference vectors. We don't, and likely won't. |
| **Neural bandits** (NeuralUCB, NeuralTS, Neural-Linear) | Break ops constraints: NN weights are KB-MB/arm (vs ~2KB), no clean warm-start from scalar benchmark, 24-dim context too small to need a NN. [Riquelme et al. ICLR 2018](https://arxiv.org/abs/1802.09127) confirmed Bayesian-linear matches or beats neural in sample-poor regimes. |
| **Deep RL** (PPO, SAC, DQN) | Bandits are 1-step; RL is for MDPs. Sample-hungry. Wrong tool. |
| **Transformer-as-bandit** (DPT, AD, PFN-TS) | Require offline pretraining corpus. Predict-latency = transformer forward pass. No clean Redis persistence. |
| **GP bandits** (GP-UCB, GP-TS) | O(N²) per update, no clean forgetting. Doesn't scale. |
| **EXP3 family** (adversarial bandits) | Regret `√(KT log K)` worse than linear `d√T` when linear model holds. |
| **Hierarchical TS** (4b in full list) | Genuine win when ≥2 providers active, but deferred — see below. |
| **IDS** (Information-Directed Sampling) | +100 LOC; consider as Phase 5+ research after FGTS-VA plateau. |

## Deferred phases — conditional on OTel/LangFuse signals

These are real wins, but each addresses a specific operational failure mode. Don't ship without OTel evidence that the symptom dominates.

| Phase | Operational problem it fixes | OTel signal to wait for |
|---|---|---|
| **3b — Failure-mode-aware cooldowns** (per error class: 429→60s, timeout→300s, 5xx→600s, auth→3600s) | LiteLLM's uniform 60s cooldown re-tries auth-broken arms every minute → wasted RPM | `dd.pareto_update_total{outcome="negative", error_class="auth_error"}` consistently > 0 over a week |
| **3b' — Per-provider circuit breaker** (fail fast when rolling per-provider error rate > 40% in 60s window) | Cascade burns through 12 NIM models that share the same upstream control-plane problem | Cascade depth > 2 events with `error_class` correlated across deployments of the same provider |
| **3b'' — Response caching** (Redis short-TTL keyed by prompt hash) | RPM ceiling hit on hot duplicate prompts (e.g. KEEP/DROP judgments on identical inputs across chapters) | `rate_limit` rate climbs near peak load + observable duplicate-prompt fingerprint via LangFuse trace bodies |
| **4a — Failure-prediction gate** (skip arms with `P(success) < τ` before bandit picks) | Bandit hasn't fully demoted a degraded arm yet → 2-3 wasted picks per regime shift | Mean cascade depth per dd_process > 1.5; large `n_obs` spread between top picks and demoted arms |
| **4b — Hierarchical TS** (provider-level latent + deployment-level offset) | "All of NIM is failing right now" takes 12 calls per dd_process to learn independently | `try_reserve_provider_slot` saturation events; observed provider-level error correlation > 0.5 across deployments |
| **5 — Classifier warm-start prior** (RouterDC-style on RouterBench/Arena data) | New deployments take ~20 calls to find their bandit level; scalar benchmark prior is 1-dim for 24-dim bandit | Frequent cold-cell `n_obs < 20` regions in `dd.pareto_n_obs` with poor `shadow_agreement` |
| **6 — Latency-budgeted cascade ordering** (rank top-K by score conditional on remaining cascade deadline) | Slow reasoning models picked at position #2 in tight-budget cascades → tail-latency miss | Cascade tail latency dominates p95/p99 of overall request latency |

## Decision rule for advancing past Phase 3c

> **When LangFuse + OpenTelemetry are wired** (deferred work; not now): review the OTel signals weekly. Whichever signal in the table above dominates — that phase ships next. If no signal dominates, ship none.

This replaces the linear sequence in the original SOTA plan with a signal-driven ordering: don't speculate, instrument first.

## Open questions deferred to LangFuse/OTel wire-up

These are the questions I'd want OTel data to answer before picking the next phase past 3c:

1. **Failure rate per error class over 7-30 days** — how common is each class, and which dominates?
2. **Provider correlation strength** — when one NIM model 429s, what fraction of other NIM models fail within 60s?
3. **Recovery-time distribution per error class** — how long until an arm next succeeds after each failure type?
4. **Reward-signal pairwise correlations** — do `(success, schema_valid, latency_ratio, hash_recall)` collapse to 1 signal in practice, or are they genuinely independent?
5. **Reward variance per cell** — is heteroscedasticity across arms strong enough to justify FGTS-VA's complexity over plain LinTS?
6. **Context-vector value** — does the bandit pick *different* deployments for different contexts within the same dd_process, or does one deployment dominate that cell regardless of context?

The first three drive bandit-family choice (4a, 4b, 3b). The last three drive whether to even keep the contextual machinery vs simplify to per-arm Beta-Bernoulli TS.

## Ship plan (revised 2026-05-23 — activated direct)

User opted to skip shadow-validation and ship FGTS-VA as the default mode in one step. The originally-staged ladder (LinUCB → LinTS → FGTS-VA across separate flags) collapsed into a single activation because FGTS-VA mathematically subsumes the earlier modes (it degenerates to LinTS when σ̂² is constant across arms, which degenerates to LinUCB at the limit of zero posterior variance).

**Live behavior:**
- Default mode: `fgts_va`
- Kill-switch ladder: `KD_DISABLE_FGTS_VA=1` → `ts`, `KD_DISABLE_BANDIT_TS=1` → `ucb` (full revert to Phase 2)
- Per-cell state migrates lazily on first `apply_update()` after deploy — no batch script required

**Risk taken on:** FGTS-VA is now in the production hot path without an A/B comparison against the previous LinUCB run. If OTel surfaces a regression (`dd.pareto_update_total{outcome="negative"}` climbs, cascade depth widens, tail latency spikes), revert via kill-switch and resume the staged-validation plan.

Resume the deferred-phases decision rule (3b / 4a / 4b / 5 / 6) only after LangFuse + OTel are wired and signal-driven prioritization is possible.

## Implementation status (2026-05-23)

**ACTIVATED — FGTS-VA is the live default.** Shadow-validation window skipped at user direction (same pattern as the 2026-05-16 LinUCB rollout). Touch points:

- [`apps/fastapi/domains/llm/rotator/bandit/constants.py`](../apps/fastapi/domains/llm/rotator/bandit/constants.py) — added `TS_SCALE`, `FGTS_VA_SIGMA_INIT_SQ`, `FGTS_VA_SIGMA_MIN_SQ`, `FGTS_VA_VAR_ALPHA`, `FGTS_FEEL_GOOD_BETA`
- [`apps/fastapi/domains/llm/rotator/bandit/types.py`](../apps/fastapi/domains/llm/rotator/bandit/types.py) — added `CellState.sigma_sq_ewma` field (with backward-compat default in `from_dict`), `ts_score()` (Phase 3a), `ts_score_va()` (Phase 3c); `apply_update()` now updates `sigma_sq_ewma` via EWMA on squared predictive residuals
- [`apps/fastapi/domains/llm/rotator/bandit/service.py`](../apps/fastapi/domains/llm/rotator/bandit/service.py) — `_resolve_mode()` env-driven dispatch, `_score_cell()` mode router, `predict()`/`predict_top_k()` accept optional `mode` override, module-level `_RNG = np.random.default_rng()` shared across coroutines

### Env vars (priority order)

```
KD_BANDIT_MODE = ucb | ts | fgts_va    # explicit selector
KD_DISABLE_BANDIT_TS = 1                # kill-switch → ucb (full revert to Phase 2)
KD_DISABLE_FGTS_VA   = 1                # kill-switch → ts  (revert one step)
(none set)                              # DEFAULT = fgts_va  ← live as of 2026-05-23
```

Per-call overrides via the `mode` kwarg on `predict()` / `predict_top_k()` win over env (used by the future shadow-A/B harness). Module emits a one-shot `[pareto] bandit scoring mode at startup: <mode>` log line per worker process so the active mode is always visible in FastAPI / Celery startup output.

### State migration

Existing Redis cells written before Phase 3c lack the `sigma_sq_ewma` field. `CellState.from_dict()` defaults missing values to `FGTS_VA_SIGMA_INIT_SQ` (0.25). On the next `apply_update()` for that cell the EWMA starts accumulating real variance estimates. **No batch migration script needed.** A_a and b_a are unchanged.

### New OTel instruments

- `dd.pareto_score` (histogram) — replaces `dd.pareto_ucb_score`. Now mode-labeled. Old dashboards filtering on the legacy name need updating to `mode="ucb"` filter on the new name.
- `dd.pareto_sigma_sq` (histogram) — per-arm noise variance estimate after each `update()`. Spread of this histogram tells you whether heteroscedasticity is real (justifies FGTS-VA over plain LinTS).
- `dd.pareto_predict_total` (counter) — added `mode` label.
- `dd.pareto_update_total` (counter) — unchanged.

### Shadow A/B harness (next step, not in this PR)

The `mode` kwarg on `predict()` / `predict_top_k()` makes shadow A/B trivial: route real traffic with the env-configured mode, but in parallel compute the counterfactual pick under another mode and emit to `dd.pareto_shadow_agreement_total{ours_mode, shadow_mode, agreement}`. Wire this in `chain/service.py` when ready to run the validation gate.

### Smoke-test result

All three score modes produce expected analytical values on a fresh cell with benchmark_prior=0.5, ψ=[1, 0, …, 0]:

| Mode | total | exploit | explore | Closed-form check |
|---|---|---|---|---|
| UCB | 0.3744 | 0.0208 | 0.3536 | exploit ≈ prior/d = 0.5/24; bonus = α·√(ψᵀA⁻¹ψ) = 0.5·√0.5 ✓ |
| LinTS | (random) | 0.0208 | (random) | mean exploit identical to UCB; sample variance ~ TS_SCALE²·A⁻¹ ✓ |
| FGTS-VA | (random + 0.0707) | (random) | 0.0707 | feel-good bonus = β·√(ψᵀA⁻¹ψ) = 0.1·√0.5 ✓ |

`apply_update(reward=0.7)` advanced `sigma_sq_ewma` from 0.25 → 0.271, matching the closed-form `0.9·0.25 + 0.1·(0.7-0.0208)² = 0.271` ✓.

JSON serialization round-trip + backward-compat with old (no-sigma-field) Redis records both verified.

## Sources

- [ParetoBandit — current stack baseline (Mar 2026)](https://arxiv.org/abs/2604.00136)
- [Agrawal & Goyal — *LinTS*, ICML 2013](https://arxiv.org/abs/1209.3352)
- [Chapelle & Li — *Empirical Evaluation of Thompson Sampling*, NIPS 2011](https://papers.nips.cc/paper/2011/hash/e53a0a2978c28872a4505bdb51db06dc-Abstract.html)
- [FGTS-VA — *Variance-Aware Feel-Good Thompson Sampling*, NeurIPS 2025](https://arxiv.org/abs/2511.02123)
- [Russac et al. — *Weighted Linear Bandits for Non-Stationary Environments*, NeurIPS 2019](https://arxiv.org/abs/1909.09146)
- [Riquelme, Tucker, Snoek — *Deep Bayesian Bandits Showdown*, ICLR 2018](https://arxiv.org/abs/1802.09127)
- [Hong & Tewari — *Hierarchical Bayesian Bandits*, AAAI 2022](https://arxiv.org/abs/2111.06929) (Phase 4b reference)
- [Russo & Van Roy — *Information-Directed Sampling*, NIPS 2014](https://web.stanford.edu/~bvr/pubs/IDS.pdf) (Phase 5+ research)
- [MOL-TS — NeurIPS 2025](https://arxiv.org/abs/2512.00930) (considered + rejected for free-tier context)
- [BaRP — *Learning to Route LLMs from Bandit Feedback*, Oct 2025](https://arxiv.org/abs/2510.07429) (future-state if we ever expose preference vectors)
- [Online Multi-LLM Selection — AAAI 2026, arXiv:2506.17670](https://arxiv.org/abs/2506.17670) (confirms 2025-2026 SOTA frontier is "better LinUCB variants", not a new family)
