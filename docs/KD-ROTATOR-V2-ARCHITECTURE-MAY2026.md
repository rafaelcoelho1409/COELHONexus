# KD LLM Rotator v2 — Architecture (May 2026)

**Status:** design doc, not yet implemented. Materializes the deferred Scope B items 4+5 (Redis pyrate-limiter, exponential cooldown) and Fix #2 v2 (adaptive pinning) from `KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` into a single coherent architecture.

**Pre-condition:** OpenTelemetry dual-export pipeline (Alloy + LangFuse) — shipped 2026-05-12 night. This document's control plane (layer 5) **requires** OTel data flowing into Mimir; without it, ~2 weeks of additional infra work would be needed.

**Total effort to industry-leading:** 12-14 engineer-days, ranked by ROI below.

---

## TL;DR

Build a 4-layer LLM rotator on top of the existing LiteLLM v1.83.14 + OTel pipeline:

1. **L1 (Data plane)** — keep LiteLLM as the execution shim. Disable its broken `adaptive_router` (no latency awareness, mandates Postgres, English-only request classifier).
2. **L2 (Decision plane)** — implement **PILOT** (Preference-Prior Informed LinUCB, EMNLP 2025 Findings) as a custom routing strategy. Per-(deployment, kd_process) bandit cell, Redis-backed state, reward function includes hash-recall and schema validity.
3. **L3 (Execution plane)** — **DDSketch-driven adaptive hedging** via `hedge-python` for tail-latency kill. Only on `grader` + `critic_faithfulness` (idempotent + latency-sensitive). Token bucket cap = 15% extra request budget.
4. **L4 (Caching plane)** — Redis-backed **L1 semantic cache** (cosine ≥ 0.97 via Nemotron Embed 1B) on `outline_creative` + `summary_creative` only. Never cache the grader/synth/refiner paths (poisons determinism).
5. **L5 (Control plane)** — Celery beat task polls Mimir PromQL every 30s, recomputes routing weights + provider-level bulkhead state. Auto-disable on 410 Gone via PromQL alert (kills NIM EOL pain).

The OpenTelemetry investment from 2026-05-12 is the moat — every other OSS gateway (Portkey, Helicone, OpenRouter, Kong AI) is blind to its own quality signal.

---

## v1 — what's already shipped (recap)

The current rotator (`services/llm_chain.py`) is genuinely production-grade for static priority:

| Layer | Component | Status |
|---|---|---|
| Execution | LiteLLM v1.83.14 with `simple-shuffle` + Redis-backed cooldown + `enable_pre_call_checks` | ✅ shipped |
| Pool curation | `kd-synth` group (4 reasoning + 4 non-reasoning frontier + 3 deep tail = 11 deployments), `kd-keylm`, `kd-reduce-label`, `kd-embed` | ✅ shipped |
| Concurrency control | `KD_LLM_GLOBAL_CONCURRENCY=10` per-process semaphore | ✅ shipped |
| Per-chapter pinning | `pick_synth_deployment(chapter.number) % len(entries)` round-robin sticky | ✅ shipped (Fix #2) |
| Surgical refine feedback | `_format_structured_output_feedback` lists first 8 missing hashes with code previews | ✅ shipped (Fix #4) |
| Observability | OTel dual-export (Alloy → LGTM + LangFuse v3) with `kd_process` metadata on every call | ✅ shipped 2026-05-12 |

What v1 **doesn't** do, that v2 fixes:

| Failure pattern | Observed | v1 response | v2 fix |
|---|---|---|---|
| Reasoning model timeout under parallel synth | 60-180s `<think>` saturates provider RPM, cascade-exhausts | Concurrency cap delays the storm but doesn't kill tail | **L3 DDSketch hedging** races non-reasoning frontier when reasoning exceeds p90 |
| Pinning trade-off (continuity vs parallelism) | Sticky deployment loses rotation, slow on reasoning | Fixed sticky from `chapter.number` hash | **L2 PILOT** adapts pin choice from observed reward; continuity emerges from `theta_a` stability |
| Hash-drop in structured output | 30-50% hash drop on multi-entity arrays even with reasoning models | Surgical refine feedback (Fix #4) — but random-walk across rotator members | **L2 PILOT reward includes hash_recall** — bandit learns which deployment respects budget |
| NIM rolling EOLs | 410 Gone every 2 weeks, cascade fails until code edit | Manual catalog refresh + redeploy | **L5 PromQL alert** auto-disables on `litellm_410_gone_total` |
| Provider-wide degradation | All NIM deployments score badly simultaneously, heuristic treats them as independent | Per-deployment cooldown applies independently | **L5 provider-level bulkhead** flips when `provider_health > 0.3` for 2 samples |

---

## v2 architecture — 4 layers + control plane

### Layer 1 — Data plane (execution shim)

**Keep LiteLLM v1.83.14.** It's the only OSS router with first-class OTel callback emission + Redis-backed cooldown + per-deployment metadata routing in one package. Stay pinned at `>=1.83.0` to avoid the March 2026 supply-chain incident on 1.82.7/1.82.8 (cf. [LiteLLM v1.83.0 release notes](https://docs.litellm.ai/release_notes/v1.83.0/v1-83-0)).

**Reject LiteLLM's beta `adaptive_router`.** Three disqualifiers ([docs](https://docs.litellm.ai/docs/adaptive_router)):
1. **No latency scoring** — "a slow model can still win on quality + cost" is explicit in their docs. For our reasoning-heavy workload where 180s `<think>` saturation IS the failure mode, ignoring latency is fatal.
2. Mandates Postgres — adds stateful service for marginal gain over Redis-LinUCB.
3. English-only regex request-type classification — won't handle our `kd_process` dimension.

**Effort: 0 days** — keep as-is, just don't enable the beta adaptive features.

---

### Layer 2 — Decision plane (PILOT contextual bandit)

**Implement PILOT (Preference-Prior Informed LinUCB)** as a `CustomRoutingStrategyBase` subclass.

Reference: *Adaptive LLM Routing under Budget Constraints* — Sarkar et al., EMNLP 2025 Findings, [arXiv:2508.21141](https://arxiv.org/abs/2508.21141). 93% of GPT-4 quality at 25% cost in their benchmarks. ~300 LoC to implement.

**Concrete formulation for KD:**

```
Context vector ψ̂(q) ∈ ℝ⁶⁴:
  embed(kd_process, chapter.number, last_refiner_iter,
        expected_hash_count, has_thinking_budget, vault_size_bucket)
  → 64-d shared projection (Nemotron Embed 1B v2, already in kd-embed group)

Per-arm state (cell key = (deployment_id, kd_process)):
  Aa: ℝ⁶⁴ˣ⁶⁴ regularized covariance
  ba: ℝ⁶⁴ expected reward vector
  θ̂a = Aa⁻¹ · ba
  
Selection:
  for each deployment a:
    score_a = ψ̂(q)ᵀ · θ̂a + α · √(ψ̂(q)ᵀ · Aa⁻¹ · ψ̂(q))   # UCB upper bound
  pick argmax_a score_a, breaking ties by lowest in-flight count

Update (after call):
  Aa ← Aa + ψ̂(q) · ψ̂(q)ᵀ
  ba ← ba + r · ψ̂(q)
  
  where r = w₁·success
         + w₂·schema_valid
         + w₃·(1 - latency_p_actual / latency_p_predicted)
         + w₄·hash_recall_ratio    # direct attack on failure pattern #3
```

**Cost policy:** the paper's ON-MCKP eligibility filter, but with `cost` redefined as `expected_wall_time × concurrency_pressure` (we're free-tier — no dollar cost to minimize). When the global semaphore is hot, reasoning models get filtered automatically.

**Warm-start:** hand-score the 11 deployments on 7 KD processes using 50-100 KD samples per `(deployment, kd_process)` cell. `λa = 1/measured_accuracy_a` gives adaptive prior strength. Saves ~1 week of exploration vs cold start.

**Redis-backed state:** `Aa, ba, θ̂a` per cell, atomic `WATCH/MULTI` updates. ~5KB per cell × ~80 cells = ~400KB total. Trivial.

**Why this beats v1 pinning:**
- Sticky pinning chosen by `chapter.number % N` is round-robin (deterministic, no adaptation). Bandit chooses by *observed reward per process per deployment* — continuity emerges from `θ̂a` stability without needing a deterministic seed.
- Hash-recall reward signal makes the bandit learn that `Mistral-Medium` drops hashes on 50-hash sections — it stops routing there for big chapters even if Mistral-Medium is fast.

**Effort: 5-7 days** including offline preference scoring.

**Rejected alternatives:**
- **Vowpal Wabbit contextual bandits** ([docs](https://vowpalwabbit.org/tutorials/contextual_bandits.html)) — separate binary, kafka-style offline tooling, overkill for 11 arms.
- **RouteLLM training pipeline** ([repo](https://github.com/lm-sys/RouteLLM)) — requires labeled eval data we don't have at scale.
- **Thompson sampling alone** — works for stationary distributions but doesn't condition on context (kd_process, chapter complexity). LinUCB is strictly better when you have features.

---

### Layer 3 — Execution plane (tail-latency kill)

**Adopt `hedge-python`** for selective hedged invocation. Uses DDSketch with 30s rolling window to learn per-host TTFT distributions; fires a backup request when primary exceeds estimated p90; token bucket caps hedge rate. This is exactly the production recipe from Dean & Barroso's [Tail at Scale (CACM 2013)](https://cacm.acm.org/magazines/2013/2/160173-the-tail-at-scale/fulltext).

**Selective application** — hedge ONLY on:
- `kd_process == "grader"` — small structured output, idempotent, on hot path
- `kd_process == "critic_faithfulness"` — single output, latency-sensitive

**Do NOT hedge:**
- `section_synth` — long output, wasted tokens on losing branch (~5K tokens lost per cancel)
- `refiner_adjustment` — needs continuity with pinned model

**Token bucket cap = 15% extra request budget.** Industry benchmark (hedge-python production reports): p99 cuts ~70% at ~9% overhead. Original Tail at Scale paper: BigTable p99.9 1800ms→74ms at 2% overhead.

**Concrete recipe:**

```python
from hedge import hedged_call_async

async def grader_call_hedged(prompt_template, invoke_vars, kd_process):
    primary_deployment = pilot.pick(kd_process, ψ̂)
    backup_deployment  = pilot.pick(kd_process, ψ̂, exclude={primary_deployment})
    
    async def call_a():
        return await litellm.acompletion(model=primary_deployment, ...)
    async def call_b():
        return await litellm.acompletion(model=backup_deployment, ...)
    
    result = await hedged_call_async(
        [call_a, call_b],
        sketch_key=f"hedge:{kd_process}",
        token_bucket_rate=0.15,   # 15% hedge budget
        delay_estimator="p90",
    )
    pilot.update(primary_deployment, kd_process, result)  # only winner trains
    return result
```

**Why this attacks failure pattern #1:** when Kimi K2.6 burns >p90 on `<think>`, Mistral L3 (non-reasoning frontier) races it and wins on grader/critic calls — where reasoning doesn't materially improve faithfulness scoring. The thinking-token overhead becomes a competitive disadvantage instead of a cascade-exhaustion trap.

**Effort: 2 days** including measuring per-deployment TTFT distributions.

---

### Layer 4 — Caching plane

**L1 semantic cache** (Redis + embedding cosine ≥ 0.97) for **`outline_creative` + `summary_creative` only**.

These two processes are:
- High cardinality but low variance — small wording differences in the input shouldn't trigger regeneration
- Token-expensive (1-3K output) — caching saves real budget
- Quality-tolerant — slight stale-cache hits are fine

Use the existing `kd-embed` group (Nemotron 1B v2) for the cache-key embedding. Cosine threshold 0.97+ to avoid false positives (industry guidance: start 0.95, tune up via observed quality regression rate).

Implementation: Redis sorted-set per process keyed by embedding, lookup via cosine over candidate set. Cache TTL 24h. Hit-rate exported as OTel metric `kd.cache_hit_ratio{kd_process=...}`.

**Do NOT cache:**
- `section_synth` — different sections within a chapter need different vault hashes; caching poisons audit
- `grader` — score must be deterministic per chapter content; cached score = audit drift
- `refiner_adjustment` — needs fresh feedback per iter; stale cache = convergence break
- `critic_faithfulness` — must reflect the assembled chapter exactly

Expected hit rate: 15-25% on the cached subset. Mostly from re-runs of the same study or similar frameworks.

**L2 vendor prompt cache** — SKIP. Mistral / NIM / Groq / Gemini free tiers don't ship prompt-cache APIs at the level Anthropic + OpenAI do. Not applicable to our pool.

**Rejected: GPTCache as full layer** ([repo](https://github.com/zilliztech/GPTCache)) — its embedding store + similarity engine is more than needed; a Redis sorted-set + numpy cosine is 100 LoC.

**Effort: 2 days.**

---

### Layer 5 — Control plane (telemetry-driven)

**Pull model:** a `RotatorController` Celery beat task runs every 30 seconds, queries Mimir via PromQL, and updates routing state in Redis.

**PromQL queries that drive decisions:**

```promql
# Provider health (fires bulkhead at > 0.3 for 2 samples)
litellm_provider_health{provider="nim"} =
  rate(otel_litellm_calls_failed_total{provider="nim"}[5m])
    / rate(otel_litellm_calls_total{provider="nim"}[5m])

# Per-deployment p50 for routing-decision tie-break
histogram_quantile(0.5,
  sum by (le, deployment_id, kd_process) (
    rate(traces_spanmetrics_duration_seconds_bucket[5m])
  )
)

# 410 Gone counter (auto-disable trigger)
sum by (deployment_id) (
  rate(litellm_requests_total{exception_class=~".*410.*|.*Gone.*"}[5m])
) > 0.01    # any sustained 410 rate
```

**Provider-level bulkhead state machine:**
```
closed → (provider_health > 0.3 × 2 samples) → open (5 min cooldown)
       ↓                                         ↓
       ← half-open (1 probe/30s, success → closed)
```

This attacks failure pattern #5: when ALL NIM deployments degrade together, the bandit learns deployment-by-deployment too slowly. The hierarchical breaker pre-empts at the provider tier (1 minute) vs the bandit's window-aggregate latency (~5 minutes).

**410 auto-disable** writes a `disabled_until=forever` flag to Redis config. Slack notification sent. No code redeploy needed. Manual intervention only to replace the model in `_synth_entries()`.

**Push model rejected** — would require an OTel collector → rotator side-channel, two more failure modes, no real latency win since 30s pull is faster than free-tier RPM budgets cycle anyway.

**Effort: 3 days** including PromQL recording rules + bulkhead state machine.

---

## Ship order (ROI ranked)

| # | Layer | Component | Effort | Failure pattern fixed | Quality bump |
|---|---|---|---|---|---|
| **1** | L3 | DDSketch hedging on `grader` + `critic_faithfulness` | **2d** | #1 reasoning timeout (p99 -70%) | Highest immediate ROI — no state, cheap |
| **2** | L5 | Provider bulkhead + 410 auto-disable | **3d** | #4 NIM EOLs, #5 provider-wide degradation | Eliminates manual rolling-EOL maintenance |
| **3** | L2 | PILOT bandit replacing static pinning | **5-7d** | #2 pinning trade-off, #3 hash-drop random walk | Core decision improvement |
| **4** | L4 | L1 semantic cache on creative processes | **2d** | Token savings (15-25% hit rate) | Cost bonus, not critical path |

**Total: 12-14 engineer-days.**

Items 1+2 alone (5d total) eliminate the two most painful failure modes. Items 3+4 are quality/efficiency upgrades.

---

## What was REJECTED as performative complexity

The research considered and explicitly rejected these — all common in 2026 LLM gateway pitches but inappropriate for our scale:

| Rejected | Why |
|---|---|
| Postgres-backed adaptive router (LiteLLM's built-in beta) | Stateful service for marginal gain over Redis-LinUCB; lacks latency scoring |
| Kong/Istio AI Gateway, Portkey self-hosted | Enterprise governance overhead at single-dev scale (cf. [Portkey vs LiteLLM 2026](https://www.pkgpulse.com/guides/portkey-vs-litellm-vs-openrouter-llm-gateway-2026)) |
| Speculative decoding | Provider-side feature — not available on Mistral/NIM/Groq/Gemini free tiers |
| Kubernetes operator pattern for routing config | Celery beat + Redis writes = 90% of value at 5% complexity |
| GPTCache as full caching layer | Redis sorted-set + numpy cosine ≈ 100 LoC, equivalent results |
| Vowpal Wabbit contextual bandits | Separate binary, Kafka-style offline tooling, overkill for 11 arms |
| RouteLLM-style trained classifier | No eval data volume to justify; would take months to collect |
| LLM-as-judge for reward signal | Performative; KD already has direct quality signal (hash_recall, schema_valid) |
| Prompt compression (LLMLingua) | <10% token savings, breaks structured output schemas |
| Model distillation | Months of work for what hedging + caching delivers in days |
| Multimodal routing | Not applicable to KD's text-only synthesis |

---

## Delta vs industry-leading

| Component | Status |
|---|---|
| LiteLLM `simple-shuffle` + cooldown + Redis | ✅ shipped → replace with PILOT custom strategy |
| Per-process metadata tagging | ✅ shipped → reused as bandit context features (free win) |
| Per-chapter pinning round-robin | ✅ shipped (Fix #2) → replaced by bandit (continuity emerges from `θ̂a` stability) |
| Global concurrency cap=10 | ✅ shipped → keep as process-level bulkhead |
| OTel dual-export (Alloy + LangFuse) | ✅ shipped 2026-05-12 → **reused as control-plane data source — the moat** |
| KD custom metrics | ✅ shipped → reused as bandit reward signal (free win) |
| DDSketch hedging | ❌ → L3, 2 days |
| Provider bulkhead + 410 alerts | ❌ → L5, 3 days |
| PILOT contextual bandit | ❌ → L2, 5-7 days |
| Semantic cache (creative only) | ❌ → L4, 2 days |

**The OpenTelemetry investment from 2026-05-12 is precisely what makes layer 5 cheap.** Without it, the control plane is 2 weeks; with it, 3 days. Every other OSS gateway (Portkey, Helicone, OpenRouter, Kong AI) is blind to its own quality signal — that's the differentiator.

---

## What "industry-leading" means for our use case

No public OSS rotator as of May 2026 ships:
1. A bandit that learns **thinking-token latency** as a routing feature
2. A reward signal tied to **structured-output hash recall**
3. **Process-aware** hedging (selective by `kd_process`, not blanket)
4. **OTel-pull telemetry** as primary control input (everyone else is metric-blind or relies on push-based observability that's slower)

After this 12-14 day investment, the KD rotator beats Portkey / Helicone / OpenRouter on this specific workload (free-tier reasoning-heavy structured-output) — not because of feature count, but because of the *quality signal* loop that none of them ship.

---

## Sources

- **PILOT — Adaptive LLM Routing under Budget Constraints**, Sarkar et al., EMNLP 2025 Findings, [arXiv:2508.21141](https://arxiv.org/abs/2508.21141)
- **The Tail at Scale**, Dean & Barroso, CACM 2013, [full text](https://cacm.acm.org/magazines/2013/2/160173-the-tail-at-scale/fulltext) — canonical hedged-request reference
- **LiteLLM v1.83.14 release notes** + adaptive_router beta docs — [release page](https://docs.litellm.ai/release_notes/v1.83.14/v1-83-14), [adaptive router](https://docs.litellm.ai/docs/adaptive_router), [router strategies](https://docs.litellm.ai/docs/routing)
- **hedge-python** PyPI package — [PyPI link](https://pypi.org/project/hedge-python/) — DDSketch-based adaptive hedging
- **Portkey: Retries, Fallbacks, Circuit Breakers in LLM Apps** — [blog post](https://portkey.ai/blog/retries-fallbacks-and-circuit-breakers-in-llm-apps/) — production circuit-breaker patterns
- **AI Gateway Caching L1+L2** — [TokenMix blog](https://dev.to/tokenmixai/ai-gateway-caching-explained-why-l1-l2-cache-layers-cut-90-of-your-llm-bill-45ab) — production cache hit rates
- **GPTCache (Zilliz)** — [repo](https://github.com/zilliztech/GPTCache) — rejected as overkill
- **RouterBench** — Hu et al., [arXiv:2403.12031](https://arxiv.org/abs/2403.12031) — LLM-router benchmark baseline
- **GAMBITTS (Generator-Mediated Bandits)** — [arXiv:2505.16311](https://arxiv.org/pdf/2505.16311) — 2025 contextual-bandit-for-LLM technique
- **SourcePilot Thompson Sampling production writeup**, Nov 2025 — [link](https://sourcepilot.co/blog/2025/11/22/how-thompson-sampling-works)
- **Vowpal Wabbit contextual bandits** — [docs](https://vowpalwabbit.org/tutorials/contextual_bandits.html) — rejected as overkill
- **RouteLLM framework** — [repo](https://github.com/lm-sys/RouteLLM) — rejected (no eval data)
- **Reasoning model thinking-token latency analysis** — [Nous Research](https://nousresearch.com/measuring-thinking-efficiency-in-reasoning-models-the-missing-benchmark)
- **Structured Output reliability 2026 (constrained decoding)** — [tianpan.co](https://tianpan.co/blog/2025-10-29-structured-outputs-llm-production)
- **LLM Speed & Latency by Provider 2026** — [BenchLM](https://benchlm.ai/llm-speed)
- **Grafana Mimir + OTel Collector configuration** — [Grafana docs](https://grafana.com/docs/mimir/latest/configure/configure-otel-collector/)

---

## Related KD docs

- `docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` — Scope A (classical-path replacements) + Scope B (rotator hardening items 1-3) + Phase B/C audit-fail hardening (Fix #1-#4). The v1 rotator is documented here.
- `docs/KD-PLANNER-REDUCE-MAY2026-OPTIMIZATION.md` — planner-side classical work (R1-R8) that produces the chapter plan this rotator serves.
