# KD Rotator — Adaptive Routing Algorithm Decision (May 2026)

**Status:** architectural decision record (ADR). Phase 2 of the v2 rotator architecture.

**Decision:** adopt **ParetoBandit** (arXiv:2604.00136, March 2026) as the adaptive routing brain. Ship behind a feature flag with shadow-mode validation before going live. Defer **PILOT** (EMNLP 2025 Findings) as a documented alternative not chosen.

**Effort:** 4-5 engineer-days. Replaces the static "PILOT 5-7 days" line item from `KD-ROTATOR-V2-ARCHITECTURE-MAY2026.md` L2.

**Prerequisites — all already shipped:**
- ✅ `services/discovery.py` — live `/v1/models` fan-out across 6 providers (235 alive models)
- ✅ `services/benchmarks.py` — 3-source benchmark fetcher + TOPSIS+z-score composite (`compute_warm_start_score` ready)
- ✅ `services/llm_chain.py` — Phase 1 dynamic catalog (top-K per step from ranked pool)
- ✅ OpenTelemetry dual-export (Alloy → Grafana LGTM + LangFuse v3) with per-call deployment_id + kd_process labels
- ✅ KD custom metrics: chapter_synth_duration, refiner_iters_to_accept, audit_missing_hashes_ratio, hash_recall_ratio

What's MISSING and being chosen now: the adaptive overlay that consumes production OTel data to reorder per-call deployment selection.

---

## TL;DR

ParetoBandit is **newer (Mar 2026 vs Aug 2025), pip-installable, and explicitly designed for non-stationary LLM serving** — exactly our case (provider EOLs, rate-limit drift, weight hot-swaps). PILOT remains the runner-up; we use its preference-prior warm-start mechanism via ParetoBandit's `(A_a^off, b_a^off)` offline statistics seeded from our existing TOPSIS+z-score composite.

Implementation pattern: feature-flag + shadow-mode + version-pinned (`paretobandit==0.1.1`) on top of the existing LiteLLM Router via a `CustomRoutingStrategyBase` subclass. Reward signal extends from "cost + quality" to **"composite of latency_z + (1 − hash_recall) + error_indicator"** — leveraging KD-specific signals from helpers.py audit gates.

---

## Background — what triggered this decision

Phase 1 (shipped 2026-05-14) replaced the static hand-curated catalog with a dynamic top-K from `rank_for_step(step, alive_models)`. This made the rotator data-driven but **not yet adaptive**: routing decisions still use LiteLLM's `simple-shuffle` over the top-K pool, with no learning from production observations.

Observed pain pre-Phase-1 that adaptive routing should address:
1. **Per-chapter pinning chose slow deployment** — round-robin (`chapter.number % N`) deterministically picked Nemotron-3-super on a NIM-rate-limited minute, wasted 6+ min retrying
2. **Hash-drop random walk** — different deployment per refine iter; refiner converged badly because feedback was on output the new model didn't generate
3. **Reasoning-model thinking-token cascade** — Kimi K2.6 / GLM-5.1 / MiniMax M2.7 burned 60-180s on `<think>` tokens, exhausting concurrency caps under parallel synth
4. **NIM rolling EOLs** — model disappears every 2-3 weeks, static catalog requires manual code edit
5. **Provider-wide degradation** — when all NIM deployments slow simultaneously, per-deployment cooldown treats them independently → slow recovery

Phase 1 partially addresses #4 (discovery surfaces EOLs in <15 min). It does NOT address #1, #2, #3, #5 — these need a learning layer.

---

## The research — May 14 2026 deep survey

A focused 15-minute research pass (subagent task `ab77eac5bda0838c2`) surveyed:
- arXiv 2025-2026 papers on contextual bandits for LLM routing
- Production LLM gateway blogs (Portkey, Helicone, OpenRouter, Kong AI, NotDiamond, Martian, RouteLLM)
- GitHub repos in the last 6 months implementing LLM-bandit routing
- OpenReview ICLR/NeurIPS/EMNLP 2025-2026 submissions

### Candidates surfaced

| # | Name | Paper | Code | Key technique |
|---|---|---|---|---|
| 1 | **ParetoBandit** | [arXiv:2604.00136](https://arxiv.org/abs/2604.00136) Mar 2026 | [github.com/ParetoBandit/ParetoBandit](https://github.com/ParetoBandit/ParetoBandit), `pip install paretobandit` (v0.1.1, Apache 2.0) | LinUCB + primal-dual budget pacer + geometric forgetting on sufficient statistics + hot-swap registry |
| 2 | **PILOT** | [arXiv:2508.21141](https://arxiv.org/abs/2508.21141) EMNLP 2025 Findings | Research code only | LinUCB with preference-prior init from offline human-pref data |
| 3 | **MixLLM** | [arXiv:2502.18482](https://arxiv.org/abs/2502.18482) NAACL 2025 | Not released | Contextual bandit + 3-layer MLP + explicit `slpen` latency penalty |
| 4 | **BaRP** | [arXiv:2510.07429](https://arxiv.org/abs/2510.07429) Oct 2025 | Research code | Feature-based bandit conditioned on model-identity features (good for transfer to new models) |
| 5 | **PROTEUS** | [arXiv:2601.19402](https://arxiv.org/abs/2601.19402) Jan 2026 | Research code | Offline RL-trained for SLA compliance |

### Production reality check (none of the major gateways ship a contextual bandit in 2026)

- **Portkey** — static conditional routing + fallbacks, no learning ([docs](https://portkey.ai/docs/product/ai-gateway/conditional-routing))
- **OpenRouter Auto** — delegates to NotDiamond's offline classifier ([docs](https://openrouter.ai/docs/guides/routing/routers/auto-router))
- **Helicone** — observability-only
- **Kong AI Gateway** — enterprise governance, no learned routing
- **ClawRoute / Kalibr** — Thompson Sampling without LinUCB's context features

This means **ParetoBandit (and our system, by adopting it) would be a genuine reference architecture** in May 2026. No public OSS gateway ships a non-stationary contextual bandit.

---

## Verification — all 3 ParetoBandit assets confirmed real

Trust-but-verify pass (2026-05-14):

| Claim | Verified |
|---|---|
| arXiv 2604.00136 paper exists | ✓ "ParetoBandit: Budget-Paced Adaptive Routing for Non-Stationary LLM Serving" by Annette Taberner-Miller, submitted 2026-03-31, revised 2026-04-14, CC BY 4.0 |
| GitHub `ParetoBandit/ParetoBandit` repo exists | ✓ Apache 2.0, **14 stars**, ~135 tests, 1,165 commits on `main` |
| PyPI `paretobandit` installable | ✓ v0.1.1, released 2026-03-29, deps: numpy/joblib/scikit-learn/tqdm + optional torch/sentence-transformers |

### Maturity concerns honestly flagged

1. **14 GitHub stars** — almost nobody is using this in production yet
2. **Single-author paper** — no co-authors, no independent replication
3. **v0.1.1 (pre-1.0)** — API may break between releases
4. **Published <2 months ago** — zero industry track record
5. **Validated on 1,824 prompts with 3 models** — our system has 12-30 deployments per step. Scale extrapolation is unproven.

These are mitigated by the shadow-mode plan below.

---

## Why ParetoBandit beats PILOT for our case

| Criterion | PILOT | ParetoBandit | Winner |
|---|---|---|---|
| Non-stationarity (model EOL, provider drift) | Stationary-prior; geometric forgetting absent | Explicit geometric forgetting on sufficient statistics + hot-swap registry | **Pareto** |
| Code availability | Research artifact (rebuild) | `pip install paretobandit` (Apache 2.0) | **Pareto** |
| Reward signal flexibility | Hardcoded cost-axis | Generic scalar — can substitute latency / hash-recall / composite | **Pareto** |
| Cold-start with benchmark prior | Yes (preference-prior is exactly this) | Yes (offline `(A_a^off, b_a^off)` initialization) | Tie |
| Peer review | EMNLP Findings 2025 | arXiv preprint only | **PILOT** |
| Replication studies | Some external citations | None yet | **PILOT** |
| Implementation cost | 5-7 days | 3-4 days (lib handles state) | **Pareto** |
| Throughput | Not benchmarked | 22k req/s single CPU, 43 μs p50 | **Pareto** |
| Production track record | Sparse | None | Tie (both unproven) |

**Tactical fallback if ParetoBandit proves over-engineered or upstream-broken:** Thompson Sampling per `(deployment, kd_process)` cell with Beta-Bernoulli updates on `hash_recall ≥ 0.85`. Ship in 1 day, gives ~80% of the value, loses context features.

---

## Implementation plan — 4 days with de-risking

### Day 1 — Dependency + warm-start integration

- Add `paretobandit==0.1.1` to `apps/fastapi/pyproject.toml` (pinned — pre-1.0)
- Create `apps/fastapi/services/pareto_bandit.py` exposing:
  - `init_bandit_state()` — populate per-(deployment, kd_process) cell with `(A_a^off, b_a^off)` from existing `services.benchmarks.compute_composite_score(...)` for warm-start
  - `pick_deployment(kd_process, context) -> str` — returns the litellm model string ParetoBandit would route to
  - `update_from_reward(deployment, kd_process, reward_components) -> None` — feeds OTel-emitted spans back into the bandit

### Day 2 — Shadow mode (observe but don't act)

- In `services/llm_chain.py`, before each `Router.acompletion(...)`:
  - Call `pareto_bandit.pick_deployment(kd_process, context)` and **log** to OTel as span attribute `kd.pareto_predicted_arm`
  - Continue routing via existing `simple-shuffle` (no behavior change)
- Add OTel metric `kd.pareto_shadow_agreement{kd_process}` — increments when ParetoBandit's pick == actual deployment that produced a successful response

### Day 3 — Reward path

- LiteLLM `success_callback` / `failure_callback` (or our existing OTel listener) extracts:
  - `latency_s` from span duration
  - `success` from response status
  - `hash_recall_ratio` from `_invoke_structured_with_fallback` result metadata
  - `schema_valid` from Pydantic `.model_validate` outcome
- Calls `pareto_bandit.update_from_reward(...)` with composite reward:
  ```
  r = w1·success + w2·schema_valid - w3·(latency_s / expected_latency_s) + w4·hash_recall_ratio
  ```
- Per-(deployment, kd_process) cell state persisted to Redis (`pareto_bandit:cell:{deployment}:{kd_process}`)

### Day 4 — Feature flag + live switch

- Add `KD_USE_PARETO_BANDIT` env var (default `"0"` — shadow mode only)
- Helm wiring: `k8s/helm/values.yaml` → `kd.useParetoBandit: "0"`
- Helm template: `_helpers.tpl` → `KD_USE_PARETO_BANDIT: "{{ .Values.kd.useParetoBandit }}"`
- When flag flips to `"1"`:
  - `_get_router()` registers ParetoBandit as the `routing_strategy` (LiteLLM `CustomRoutingStrategyBase`)
  - Falls back to `simple-shuffle` if ParetoBandit raises
- Watch `kd.pareto_shadow_agreement` for 1-2 weeks; flip flag to `"1"` only after agreement > 60% AND no error spikes

---

## Risk mitigation summary

| Risk | Mitigation |
|---|---|
| ParetoBandit lib upstream-breaks | Pin `==0.1.1` in pyproject.toml; do not auto-upgrade until v1.0 |
| Cold-start exploration burn | Warm-start `(A_a^off, b_a^off)` from TOPSIS composite — Day 1 behavior = Phase 1 behavior |
| Algorithm misbehaves on our scale | Shadow mode for 1-2 weeks before flipping live; logged predicted_arm vs actual_arm in OTel |
| Single-author research has bug | Fallback path: feature flag flips back to `simple-shuffle` |
| Reward function miscalibration | Per-component weight knobs in Helm values; tunable without redeploy |
| Hot-swap (new model added by discovery) lags | ParetoBandit's `hot-swap registry` claims ~142 steps to onboard; supplement with explicit pool-refresh on discovery's EOL/new event |

---

## Future direction — River integration (Phase 3+)

After ParetoBandit ships and stabilizes, the next quality lift comes from layering **drift detection** on top of the bandit's geometric forgetting. The candidate is the [River](https://riverml.xyz) online-ML library.

### Best River modules to add (Phase 3, ~2 days)

| Module | Purpose | Maps to which COELHO Nexus signal |
|---|---|---|
| **`river.drift.ADWIN`** (Adaptive Windowing) | Statistical test that data distribution shifted | Per-(deployment, kd_process) success rate or latency p50. Triggers explicit pool-refresh + bandit posterior re-init when drift detected — faster than geometric forgetting's gradual decay. |
| **`river.stats.RollingQuantile`** | Efficient O(log n) per-deployment p50/p90/p99 latency | Replaces ad-hoc latency tracking with a robust streaming quantile estimate. Feeds DDSketch hedging (v2 L3). |
| **`river.metrics.Rolling*`** (RollingAccuracy, RollingF1, etc.) | Windowed success/fail rates with configurable window | Per-(deployment, kd_process) recent success rate. Forms ParetoBandit's reward context. |
| **`river.anomaly.HalfSpaceTrees`** | Online anomaly detection | Flag suspicious deployments mid-run — e.g., a model that was reliable but now has a string of timeouts (catches whole-provider degradation faster than per-deployment cooldown). |

### The single best River algorithm — `river.drift.ADWIN`

If we had to pick ONE River algorithm to ship alongside ParetoBandit, it's **`ADWIN` (Bifet & Gavaldà, 2007)**. Rationale:

- **Direct mechanism for provider regression** — ParetoBandit's geometric forgetting decays old observations over time. ADWIN gives an explicit *statistical test* that drift happened, triggering immediate response (not gradual decay).
- **Per-deployment per-kd_process granularity** — instantiate an ADWIN detector per `(deployment, kd_process)` cell. When ADWIN raises a drift event, force ParetoBandit to re-initialize that cell's `(A_a, b_a)` from the current benchmark composite (treat as cold start).
- **Battle-tested** — referenced in 2,300+ papers, in River since v0.1, used in production at scale (e.g. PWPAE concept-drift framework, IEEE GlobeCom 2021).
- **Cheap** — O(log W) per observation where W = window size; no model retraining needed.

### Composite Phase 3 architecture

```
                         OTel spans (per-call latency, success, hash_recall)
                                              ↓
                         ┌────────────────────┴────────────────────┐
                         │                                          │
                         ▼                                          ▼
              river.drift.ADWIN(success_rate)        river.stats.RollingQuantile(latency)
              per-(deployment, kd_process)            per-(deployment, kd_process)
                         │                                          │
                         │ drift detected                            │ p50, p90, p99
                         ▼                                          ▼
                  ParetoBandit                          DDSketch hedging trigger
                  (re-init cell θ̂_a from benchmark)    (race backup when primary > p90)
                         │
                         ▼
                  Updated UCB scores → next routing decision
```

This pairs ParetoBandit's slow geometric forgetting with River's fast drift alarm. Belt + suspenders for non-stationarity.

### What NOT to use River for

- **Replacing ParetoBandit's bandit logic** — River's `river.bandit.UCB1` / `ThompsonSampling` lack the context features (kd_process, chapter complexity) that ParetoBandit's LinUCB structure provides. River bandits are a tactical *fallback*, not a replacement.
- **Training a routing classifier** — overlaps with rejected RouteLLM-style approach; we don't have labeled eval data at scale.
- **Replacing scikit-learn's TOPSIS scoring** — different domain (online vs batch ranking).

---

## What was REJECTED in this decision (and why)

| Rejected option | Why |
|---|---|
| Build PILOT from scratch (originally planned) | Newer ParetoBandit dominates on non-stationarity, hot-swap, code availability |
| Wait for ParetoBandit v1.0 stable release | Shadow mode + version pin de-risks v0.1.x; waiting delays showcase value |
| MixLLM | Code not released; latency penalty not query-conditioned (global queue-state only) |
| PROTEUS (offline RL) | Beautiful but requires labeled training data we don't have |
| BaRP | Good for model-transfer but doesn't address non-stationarity as directly as ParetoBandit |
| Thompson Sampling alone (River or custom) | Loses context features that improve routing on `(deployment, kd_process)` cross-term |
| Wait for production OTel data to accumulate first | ParetoBandit's warm-start mechanism means Day 1 routing = benchmark-prior routing = Phase 1 routing. No data wait needed. |

---

## Sources

- [ParetoBandit paper (arXiv:2604.00136)](https://arxiv.org/abs/2604.00136)
- [ParetoBandit GitHub](https://github.com/ParetoBandit/ParetoBandit) — Apache 2.0, 14 stars, 1,165 commits
- [ParetoBandit PyPI](https://pypi.org/project/paretobandit/) — v0.1.1 released 2026-03-29
- [PILOT paper (arXiv:2508.21141)](https://arxiv.org/abs/2508.21141) — EMNLP 2025 Findings
- [MixLLM paper (arXiv:2502.18482)](https://arxiv.org/abs/2502.18482) — NAACL 2025
- [BaRP paper (arXiv:2510.07429)](https://arxiv.org/abs/2510.07429) — Oct 2025
- [PROTEUS paper (arXiv:2601.19402)](https://arxiv.org/abs/2601.19402) — Jan 2026
- [Portkey conditional routing](https://portkey.ai/docs/product/ai-gateway/conditional-routing)
- [OpenRouter Auto Router](https://openrouter.ai/docs/guides/routing/routers/auto-router)
- [River ML library](https://riverml.xyz)
- [River drift detection](https://riverml.xyz/dev/api/drift/ADWIN/)
- [PWPAE: Concept Drift Adaptation with River](https://github.com/Western-OC2-Lab/PWPAE-Concept-Drift-Detection-and-Adaptation)

---

## Cross-references

- `docs/KD-ROTATOR-V2-ARCHITECTURE-MAY2026.md` — original v2 design (this doc supersedes its L2 PILOT section)
- `docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` — v1 rotator hardening (Scope A + B + fixes #1-#4)
- `docs/KD-SESSION-2026-05-12-FINDINGS.md` — production failure modes that motivate adaptive routing
- `apps/fastapi/services/llm_chain.py` — Phase 1 dynamic catalog
- `apps/fastapi/services/discovery.py` — provider discovery layer
- `apps/fastapi/services/benchmarks.py` — composite scoring + warm-start mechanism
