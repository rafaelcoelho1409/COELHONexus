# KD Rotator — Always-On ParetoBandit with 4 Enhancements (May 2026)

**Status:** active implementation. Supersedes the 3-mode flag plan in `docs/KD-ROTATOR-PARETO-BANDIT-DECISION-MAY2026.md` §Implementation.

**Decision:** ship the bandit always-on from day 1 — warm-started so its behavior IS Phase 1 on day 1, with no manual mode-flipping. Augment the basic LinUCB with 4 enhancements that distinguish "works" from "best possible."

**Why no flag-driven modes:** the warm-start mechanism makes `shadow` vs `live` a non-decision. Day 1 cell state is the benchmark composite → bandit picks = Phase 1's top-K pick. There's no risk window to gate with `KD_USE_PARETO_BANDIT="shadow"` — the bandit's day-1 behavior is already validated (Phase 1 has been running). It's the LEARNING that's new.

Single escape hatch retained: `KD_PARETO_BANDIT_DISABLE=1` for emergency disable. Default unset = bandit runs.

---

## TL;DR

```
Lifespan startup:
   warm-start cells from benchmark composite          (already shipped Phase 2 Day 1)
   register LiteLLM success_callback + failure_callback for reward attribution
   
Every LLM call (via _invoke_structured_with_fallback):
   1. build_context_vector(kd_process, chapter, hashes, time_of_day, recent_load_per_provider)
   2. ranked = pareto_bandit.predict_top_k(kd_process, context, candidates, k=3)
   3. for each deployment in ranked:
        chain = build_pinned_chain_any(deployment_id, group)
        try:    result = await chain.ainvoke(...)
        except: submit_reward(deployment, kd_process, context, -reward_by_error_class)
                continue
        submit_reward(deployment, kd_process, context, +reward)
        return result
   4. all 3 bandit picks failed → fall back to Phase 1 simple-shuffle Router (untouched)

Background (every 60s, Celery beat):
   for cell in all_cells:
     if river.drift.ADWIN(success_rate).update(latest):
       cell.reset_from_benchmark()
       emit_metric("kd.pareto_drift_reset", labels={deployment, kd_process})
```

No flag. No human in the loop. Automatic learning + automatic drift recovery.

---

## The 4 enhancements that make it "best" not just "works"

### Enhancement 1 — Multi-signal reward (rate-limit-aware)

**Problem:** the basic reward treats all failures the same. 429 (rate-limited; retry later) and 500 (model broken; avoid) currently produce identical negative reward, so the bandit can't distinguish "this deployment is overloaded right now" from "this deployment is structurally bad."

**Fix:** extend `compose_reward()` to accept `error_class`:

```python
def compose_reward(*, success, schema_valid=False, latency_s=None,
                   expected_latency_s=None, hash_recall=None,
                   error_class=None) -> float:
    # Success path: +0.30·success + 0.25·schema + 0.20·latency_signal + 0.25·hash_recall
    # Failure path: penalty depends on error_class
    error_penalty = {
        None:                  0.0,        # success
        "rate_limit":         -0.10,        # 429 — try later, not structural failure
        "timeout":            -0.30,        # likely deployment overload
        "server_error":       -0.50,        # 5xx — deployment broken
        "auth_error":         -0.80,        # 401/403 — config wrong, avoid
        "schema_invalid":     -0.40,        # produced unparseable output
    }
```

The bandit learns "429 = waiting room, 500 = avoid entirely."

### Enhancement 2 — Temporal + load context features (24 dims, was 16)

**Problem:** the 16-dim context vector is request-side only. NIM is fast at 3am Brazil, throttled at 2pm — but the bandit can't tell because `hour_of_day` isn't a feature.

**Fix:** extend `make_context_vector()` to 24 dims:

```
[0]     constant bias                                  (1 dim)
[1]     chapter_number_normalized                      (1 dim)
[2]     expected_hash_count_normalized                 (1 dim)
[3]     has_thinking_budget                            (1 dim)
[4-6]   vault_size_bucket (small/medium/large)         (3 dims, one-hot)
[7-15]  kd_process one-hot                             (9 dims)
[16]    hour_of_day_sin    sin(2π·hour/24)             (1 dim) — diurnal cycle
[17]    hour_of_day_cos    cos(2π·hour/24)             (1 dim) — same, orthogonal
[18]    day_of_week_normalized   (weekday/6)           (1 dim)
[19-24] recent_5min_error_rate_per_provider           (6 dims, one per enabled provider)
```

The temporal features use sin/cos encoding so the bandit understands "23:00 and 00:00 are adjacent." The per-provider recent error rate captures "NIM is currently degrading, even if our pinned NIM deployment looks OK."

### Enhancement 3 — Top-K cascade routing (not top-1 pin)

**Problem:** pinning to ParetoBandit's #1 pick bypasses LiteLLM's 40-deep cascade entirely. If the pinned model is 429-cooled, the call fails immediately — there's no automatic fallthrough.

**Fix:** ParetoBandit returns top-K=3 ranked. helpers.py iterates: try #1, on fail submit reward + try #2, on fail submit reward + try #3. Final fallback after all 3 fail = original Phase 1 simple-shuffle Router (which still has the full 40-deep cascade).

```python
ranked_top_3 = pareto_bandit.predict_top_k(kd_process, context, candidates, k=3)

for deployment in ranked_top_3:
    try:
        chain = build_pinned_chain_any(deployment, group)
        result = await chain.ainvoke(...)
        submit_reward(deployment, kd_process, context, +reward)
        return result
    except RateLimitError as e:
        submit_reward(deployment, kd_process, context, error_class="rate_limit")
        continue
    except TimeoutError as e:
        submit_reward(deployment, kd_process, context, error_class="timeout")
        continue
    except Exception as e:
        submit_reward(deployment, kd_process, context, error_class="server_error")
        continue

# All 3 bandit picks failed → fall back to Phase 1 chain
return await original_llm.ainvoke(...)
```

This gives the bandit faster failover AND failure-class-aware reward attribution. Best of both: bandit smarts on the happy path, Phase 1 robustness on the unhappy path.

### Enhancement 4 — `river.drift.ADWIN` for regime-shift detection

**Problem:** geometric forgetting (γ=0.01) decays at constant rate. When a provider has a SUDDEN quality regression (e.g., NIM silently hot-swaps GLM-5.1 weights to a worse checkpoint), the bandit takes 100+ updates to adapt. That's an entire study lost.

**Fix:** per cell, run ADWIN (Adaptive Windowing) on the success_rate stream. When ADWIN raises a drift alarm, reset that cell from the current benchmark composite — explicit re-init faster than waiting for geometric decay.

ADWIN's mechanics: maintains a variable-width window over the success/fail stream. When two adjacent sub-windows have statistically distinct means, declares drift, drops the older data. Bifet & Gavaldà 2007. Battle-tested, ~2,300 citations.

Implementation: background task every 60s iterates all cells, calls `cell.adwin.update(cell.recent_success_rate)`, on drift event resets cell. Adds `river>=0.21` dependency.

```python
# Background task, scheduled via Celery beat every 60s
from river import drift

async def drift_check_all_cells():
    cells = await pareto_bandit.get_all_cells(redis=redis)
    for cell in cells:
        adwin = _adwin_per_cell.setdefault(cell.key(), drift.ADWIN())
        # cell.recent_success_indicator is 1.0 if last call succeeded, 0.0 otherwise
        change_detected = adwin.update(cell.recent_success_indicator)
        if change_detected:
            # Drift! Reset cell from current benchmark composite
            new_score = await benchmarks.get_benchmarks(canonicalize(cell.deployment))
            cell.reset_to_benchmark_prior(new_score)
            await pareto_bandit.save_cell_state(cell, redis=redis)
            logger.warning(
                f"[pareto-drift] {cell.deployment}/{cell.kd_process} drift detected → reset"
            )
            _record_drift_reset(cell.deployment, cell.kd_process)
```

Belt + suspenders: ADWIN catches the fast shifts, geometric forgetting handles slow decay. Both run in parallel.

---

## Files modified — full inventory

| File | Change | LoC delta |
|---|---|---|
| `services/pareto_bandit.py` | Extend context 16→24, multi-signal `compose_reward`, `predict_top_k`, cell drift state. | +120 |
| `services/llm_chain.py` | Add `build_pinned_chain_any(deployment_id, group)` (generalize existing kd-synth pinning). | +25 |
| `graphs/knowledge/helpers.py` | Wrap `_invoke_structured_with_fallback` with bandit pre-call + post-call hooks; top-K cascade routing. | +80 |
| `app.py` | Register LiteLLM `success_callback` + `failure_callback` for reward extraction. Remove KD_USE_PARETO_BANDIT 3-state. | +35, -15 |
| `services/pareto_drift.py` | NEW — background ADWIN drift checker. | +180 (new file) |
| `celery_app.py` | Add Celery beat schedule for drift_check_all_cells every 60s. | +15 |
| `routers/v1/admin/rotator.py` | Extend `/bandit-state` with `recent_drift_resets` + per-cell ADWIN state. | +25 |
| `pyproject.toml` | Add `river>=0.21` dependency. | +1 |
| `k8s/helm/values.yaml` + `_helpers.tpl` | Drop `useParetoBandit` 3-state, add `paretoBanditDisable` boolean (default "0"). | +5, -10 |
| `services/pareto_bandit.py` shadow-mode helper | Remove (not needed in always-on architecture). | -50 |

**Net code:** ~+440 LoC, -75 LoC = +365 LoC delta.

---

## Implementation order — single sprint

**Pass 1 — Core (3 hours):**
1. Extend `services/pareto_bandit.py`:
   - `CONTEXT_DIM = 16` → `24`
   - `make_context_vector(...)` accepts `time_now`, `recent_error_rates: dict[provider, float]`
   - `compose_reward(...)` accepts `error_class`
   - New: `predict_top_k(kd_process, context, candidates, k=3) -> list[tuple[deployment, ucb_score]]`
   - New: `cell.recent_success_indicator` field for ADWIN
2. Add `build_pinned_chain_any(deployment_id, group)` to `services/llm_chain.py`
3. Add `river>=0.21` to `pyproject.toml`

**Pass 2 — Reward callback path (2 hours):**
1. `app.py` lifespan: register `litellm.success_callback`, `litellm.failure_callback`
2. Callbacks extract `(deployment, kd_process, latency, error_class)`, submit reward via thread-safe asyncio
3. Test deployment extraction across LiteLLM response shapes

**Pass 3 — Bandit-driven routing (2 hours):**
1. Modify `helpers._invoke_structured_with_fallback`:
   - Pre-call: build context vector (with time + recent load), get candidates from active catalog, predict_top_k
   - Iterate top-K with per-attempt error-class reward submission
   - Final fallback to Phase 1 chain on all-fail

**Pass 4 — Drift detection (2 hours):**
1. New `services/pareto_drift.py`:
   - Per-(deployment, kd_process) ADWIN state in Redis
   - `drift_check_all_cells()` async function
2. Celery beat task scheduled every 60s
3. On drift: reset cell from current benchmark composite

**Pass 5 — Cleanup + verification (1 hour):**
1. Remove `KD_USE_PARETO_BANDIT` 3-state logic from `pareto_bandit.py` + `app.py`
2. Helm wiring: drop `useParetoBandit`, add `paretoBanditDisable` boolean
3. Math sanity tests (compose_reward, predict_top_k, ADWIN)
4. Helm template renders correctly

**Total: 10 engineer-hours.**

---

## What this gives you that the basic always-on bandit doesn't

| Capability | Basic always-on bandit | This architecture |
|---|---|---|
| Day-1 routing matches Phase 1 (no risk) | ✓ (warm-start) | ✓ |
| Learns from production calls | ✓ | ✓ |
| Distinguishes 429 from 500 from timeout | ✗ — all "negative" | ✓ (multi-signal reward) |
| Adapts to diurnal load patterns (NIM peak vs idle) | ✗ — no time context | ✓ (sin/cos hour) |
| Cascades through top-3 picks before falling back to Phase 1 | ✗ — pin top-1 only | ✓ (top-K iteration) |
| Detects sudden provider quality regression in <2 min | ✗ — gradual decay only | ✓ (ADWIN drift alarm) |
| Auto-recovers from provider hot-swap (model weights changed silently) | ✗ — 100+ obs to adapt | ✓ (drift → reset) |
| User runs `skaffold dev` and never touches a flag | ✓ | ✓ |

---

## Failure modes + fallbacks (defense in depth)

1. **Bandit raises in `predict_top_k`** → exception caught at helpers.py level, falls back to original `llm` chain (Phase 1 simple-shuffle). Studies never break.
2. **All 3 bandit picks fail** → falls back to original `llm` chain (full 40-deep cascade). Worst case = Phase 1 behavior.
3. **LiteLLM callback fails to extract deployment** → reward update skipped (logged), bandit doesn't learn that call. Doesn't poison state.
4. **River ADWIN not installed** → drift check task no-ops gracefully. Bandit still learns via geometric forgetting.
5. **Redis unavailable** → cell reads return None → bandit treats every cell as fresh (max exploration). Studies still run via Phase 1 fallback.
6. **`KD_PARETO_BANDIT_DISABLE=1`** → entire bandit pre-call hook bypassed. Pure Phase 1 behavior. Emergency stop.

---

## What was REJECTED in this design

| Rejected | Why |
|---|---|
| `KD_USE_PARETO_BANDIT="shadow"` / `"live"` 3-mode flag | Warm-start makes the modes equivalent on day 1; flag adds operator burden without correctness gain |
| Always pin to top-1 | Bypasses LiteLLM's cascade; one cooled-down pick = call fails |
| Reward update from response metadata (without pinning) | Reward attribution unreliable when LiteLLM picks the actual deployment via simple-shuffle |
| ADWIN as primary forgetting mechanism (no geometric decay) | ADWIN only detects FAST shifts; slow trends still need geometric decay |
| Static expected_latency for all deployments | Per-deployment learned baseline is better; deferred to Phase 3 |
| LiteLLM `CustomRoutingStrategyBase` subclass | Would require deep LiteLLM internals integration; pinning per call is simpler + equally effective |

---

## Sources + dependencies added

- ParetoBandit: [arXiv:2604.00136](https://arxiv.org/abs/2604.00136), [github.com/ParetoBandit/ParetoBandit](https://github.com/ParetoBandit/ParetoBandit), [PyPI](https://pypi.org/project/paretobandit/) — algorithm reference (our implementation, not the lib directly)
- River: [riverml.xyz](https://riverml.xyz) — `river.drift.ADWIN` for regime-shift detection
- ADWIN: Bifet & Gavaldà, "Learning from Time-Changing Data with Adaptive Windowing", SDM 2007 — drift-detection foundation
- LiteLLM callbacks: [docs.litellm.ai/docs/observability/custom_callback](https://docs.litellm.ai/docs/observability/custom_callback) — reward attribution mechanism

---

## Cross-references

- `docs/KD-ROTATOR-PARETO-BANDIT-DECISION-MAY2026.md` — original ParetoBandit decision (this doc supersedes its Implementation §)
- `docs/KD-ROTATOR-V2-ARCHITECTURE-MAY2026.md` — full v2 design including L3 hedging + L5 bulkhead (Phase 3+)
- `services/pareto_bandit.py` — bandit core
- `services/pareto_drift.py` — NEW, ADWIN drift detection (to be created)
- `services/llm_chain.py` — Phase 1 dynamic catalog
- `graphs/knowledge/helpers.py` — call site for bandit-driven routing
