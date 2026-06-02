# KD Speed Optimization Plan (2026-05-14)

**Context:** canary v7 (first end-to-end clean run after v1-v6 architectural fixes) **hit Celery `SoftTimeLimitExceeded` at 2h 1min** with 4 chapters committed (ch01 + ch07 cache; ch02 + ch04 fresh ACCEPT) and ch03/ch05/ch06 mid-refine. Architecture didn't fail — the Celery task ceiling did. This makes the optimization plan **the binding constraint to fit any study into Celery's window**, not just a nice-to-have.

**Hard ceiling discovered:** `CELERY_TASK_SOFT_TIME_LIMIT` ≈ 2h (7200s). Either raise it OR the per-study wall-clock must drop below it. The optimizations below target the latter.

**Read first:** `docs/KD-SESSION-2026-05-14-FINDINGS.md`, `docs/KD-NEXT-STEPS-2026-05-14.md`.

---

## v7 baseline measurements

**Study:** Terragrunt latest senior, concurrency=3, 9 chapters.
**Started:** 14:38:34 BRT (17:38:34 UTC) on 2026-05-14.
**First real work:** 17:44:22 UTC (5min 48s lost to fresh worker init).
**Outcome:** `state=FAILURE`, `error=SoftTimeLimitExceeded()` at 19:39:52 UTC = **2h 1min** wall-clock.
**Final state when timer fired:**

| Chapter | State | Detail |
|---|---|---|
| ch01 | ✅ cache | score 0.82, instant |
| ch07 | ✅ cache | score 0.89, instant |
| ch04 | ✅ iter 0 ACCEPT | score 0.93, deployment glm-5.1, **9 min** chapter |
| ch02 | ✅ iter 2 ACCEPT | score 0.89, after Self-Refine 41 missing → 0 missing → 4 thin/accept. **~1h 43min** chapter (20-min OUTER_TIMEOUT burn + 3 iterations) |
| ch03 | iter 0→1 refine | monster chapter, 170 hashes, 20 sections — still in flight |
| ch05 | iter 1→2 refine | 62 hashes, 9 sections — still in flight |
| ch06 | pinned, starting | just entered batch |
| ch08, ch09 | not started | queued |

**Aggregate retries on /chat/completions:** 41 (vs v6 had hundreds at the same elapsed time — semaphore preventing rate-limit cascades).

**No TERMINAL FAILURE. No `re-pick N/3` cascade-exhaustion events.**

---

## Root causes of slowness (in order of impact)

### 1. `OUTER_TIMEOUT = 1200s` (20 min) too generous

**Evidence:** ch02 pin at 17:44:24, first `RuntimeError` (timeout) at 18:04:25 = **exactly 20 minutes** wasted on a stuck deepseek-v4-flash call. The semaphore + cascade did not save this time — the call was actively (apparently) running, no rate-limit signal, so the inner LiteLLM Router never fell back. Only the outer asyncio.wait_for fired.

**Code:** `apps/fastapi/graphs/knowledge/helpers.py:1670` — `OUTER_TIMEOUT_SECONDS = 1200`.

**Cost:** ~20 min per stuck call. Across a study with N chapters, expect 1-2 stuck calls. **~30-40 min saved per study.**

### 2. UMAP numba JIT pre-warm in first task path (65s)

**Evidence:** `[worker-init] UMAP numba JIT pre-warmed in 65.5s (production params, 20×128 dummy)` at 17:41:22 — exactly 3 minutes after task received. The first task waited for this.

**Code:** Look in `apps/fastapi/celery_app.py` worker_process_init hook or `tasks/knowledge/distiller.py` task entry. Currently fires lazily.

**Fix:** Move JIT pre-warm into the Celery `worker_process_init` signal handler so the worker is ready BEFORE the first task arrives. Workers always pay this cost on fork; might as well pay it while idle.

**Cost saved:** 65s every Celery worker restart (after every skaffold redeploy). **~1 min per study on cold worker, 0 on warm.**

### 3. Per-provider semaphore caps effective concurrency at 2 (not 3)

**Evidence:** v7 launched with `max_concurrent_chapters=3`. Bandit pinned all 3 first-batch chapters to NIM deployments (glm-5.1, deepseek-v4-flash, kimi-k2.6). Per-provider semaphore `nvidia_nim=2` then serialized them — 2 chapters concurrent, 3rd waited ~9 min for first slot.

**Why bandit picked all-NIM:** NIM has the most models in the kd-synth pool, and benchmark-prior TOPSIS ranks NIM models top by composite score. The bandit's UCB top-K is deterministic given priors → all NIM.

**Code:** `apps/fastapi/graphs/knowledge/helpers.py:_PROVIDER_CONCURRENCY` (added 2026-05-14) + `apps/fastapi/services/llm_chain.py:pick_synth_deployment_bandit` (chapter-pin reservation only at deployment level, not provider level).

**Fix options (ordered by simplicity):**

- **Provider-aware chapter-pin reservation**: when reserving `nvidia_nim/glm-5.1`, ALSO reserve a `provider:nvidia_nim:slot` counter (incr/decr with TTL). When the counter ≥ 2, skip ALL nvidia_nim deployments and prefer Groq/Cerebras/Mistral/Gemini. This restores the original concurrency=3 wall-clock without rate-limit risk.
- **Provider-tier UCB blend**: penalize the UCB score for an arm whose provider's current load is high. Smooth alternative to hard reservation; bandit becomes load-aware.
- **Raise per-provider caps but add Redis-backed rate-limit budget tracking**: more invasive, requires per-provider RPM counters synced across Celery workers (Scope B item #4).

**Cost saved:** wall-clock reduction from `chapters/2` to `chapters/3` parallel throughput = **~33% chapter-batch speedup** (3 chapters in 9 min instead of 13.5 min).

### 4. Self-Refine cost: each iter re-runs all Phase C section calls

**Evidence:** ch02 iter 0 had 41 missing of 47 hashes → iter 1 had 0 missing but 8 thin → iter 2 had 4 thin/accept. Each iter required full Phase C redo (~20 calls × few minutes each). **~20 min per iter × 3 iters = ~1h** of LLM time for ch02 alone.

**The waste:** iter 1 had **no missing hashes** — it fully recovered content. iter 2 only had to fix 8 thin sections. But the refiner runs Phase C from scratch each time, including re-generating the 39 sections that were already complete.

**Fix:** **per-section result caching keyed by `(chapter_id, section_idx, content_hash, deployment)`**. On refine iter 2, only re-generate sections flagged thin/missing in iter 1's audit. Pass through cached results for the rest.

**Risk:** the refiner's holistic awareness matters — re-running section 5 when section 4 changed might produce better section 5. But for thin/missing-only fixes (no semantic dependency), surgical re-run is safe.

**Cost saved:** for chapters needing 2-3 refine iters (most non-cache chapters), **~40-50% of total Phase C time**. Could be 20-30 min per refine-loop chapter. **Massive.**

### 5. Per-call bandit cascade overhead

**Evidence:** every section call in Phase C goes through `_invoke_structured_with_fallback` which:
- builds 25-dim context vector
- queries Redis for cell state of all candidates (parent pool, ~25 deployments)
- runs UCB sort
- iterates top-K=3 calling `build_pinned_chain_any` per iteration

Overhead per call: estimated 50-200ms across Redis roundtrips + cell hydration + chain construction. For a chapter with ~20 section calls plus grader plus critic, that's ~30 LLM-cascade invocations = 1.5-6 seconds of bookkeeping per chapter.

**Fix:** cell-state batch fetch (single Redis MGET for parent pool) + per-process LRU cache of recent cell states with 30s TTL. Pinned-chain cache already exists.

**Cost saved:** ~3-5 seconds per chapter. **~30s per study.** Smallest of the candidates; defer.

### 6. Monster chapter (ch03) section-call serialization

**Evidence:** ch03 has 170 hashes → bucket-split to 20 sections → 20 sequential Phase C calls. Each call on NIM = ~3 min. Total Phase C: ~60 min single-threaded.

**Why serialized:** Phase C calls within a single chapter run sequentially in the code; the chapter is a single async task.

**Fix:** within-chapter section call parallelism. Use `asyncio.gather` for non-dependent section calls (most are independent — section 1's output doesn't feed section 2). The semaphore + per-provider limits will naturally cap actual concurrency.

**Risk:** section calls share the prompt budget; running them in parallel might trigger more rate limits. With the per-provider semaphore in place, this is now controllable.

**Cost saved:** monster-chapter time drops from ~60min to ~20-30min depending on actual provider parallelism. **~30 min per monster chapter.**

---

## Ranked optimization plan

| # | Optimization | LoC | Effort | Speedup | Risk |
|---|---|---|---|---|---|
| 1 | OUTER_TIMEOUT 1200→300 | 1 | 5 min | 30-40 min per stuck call | low (most calls <3min anyway) |
| 2 | UMAP pre-warm at worker init | ~10 | 15 min | 65s per cold start | nil |
| 3 | Provider-aware chapter-pin reservation | ~40 | 1h | 33% chapter-batch speedup | low; preserves bandit semantics |
| 4 | Per-section cache across refine iters | ~80 | 2-3h | 40-50% refine-loop time | medium (validate refiner still converges) |
| 5 | Within-chapter section-call parallelism | ~30 | 1h | 50% monster-chapter time | medium (more rate limit pressure) |
| 6 | Bandit cell-state batch fetch + LRU | ~50 | 1h | seconds per chapter | nil |

**Total expected speedup: 3-5x** end-to-end on a typical 9-chapter study (from ~2h to ~25-40 min).

**Suggested ship order:** 1 + 2 + 3 first (cheap, high-impact, low-risk). Then 4 + 5 once 1-3 are validated. 6 is icing.

---

## Quality preservation principles

Each optimization must not regress quality. The non-negotiables:

- **Self-Refine still converges**: if optimization #4 (per-section cache) makes the refiner miss cross-section consistency, undo it.
- **Audit gate still detects defects**: don't bypass the audit just because iter 0 was "probably good" — the 41-missing-hash detection for ch02 was the gate doing its job.
- **Bandit observations still accumulate**: any caching must not skip the per-call reward submission. The bandit needs to learn from every call.
- **Acceptance threshold stays at 0.85**: don't lower it to make more chapters pass. Quality floor is set.
- **No early termination of refine on iter 0 thin**: ch04 hit 0.93 at iter 0 = legitimate. Don't change the threshold to declare iter 0 success more aggressively; pad the audit detection if anything.

---

## Open questions for the next session

1. Is the OUTER_TIMEOUT=1200 a backstop or commonly hit? Need to instrument: log per-call latency p50/p95/p99 over a study, then size the timeout from data not intuition.
2. Per-section cache key design: `(chapter_id, section_idx, content_hash, deployment)` — but what if the prompt template version changes? Need a template-hash component.
3. Provider-aware reservation: how to detect "provider slot full" cheaply? Per-provider Redis counter with EXPIRE = chapter-pin TTL?
4. Within-chapter parallelism: what's the right concurrency cap per chapter? Probably 3-5 sections in flight at once.
5. Are there sub-optimizations to the planner step (currently ~1 min for cached + ~3 min for fresh)? Worth instrumenting.

---

## Cross-references

- `docs/KD-NEXT-STEPS-2026-05-14.md` — prior architectural plan (mostly shipped now)
- `docs/KD-SESSION-2026-05-14-FINDINGS.md` — full session log including v4-v7 canary observations
- `apps/fastapi/graphs/knowledge/helpers.py` — `_invoke_structured_with_fallback`, semaphores, OUTER_TIMEOUT
- `apps/fastapi/services/pareto_bandit.py` — `try_reserve`, `predict_top_k` (provider-aware reservation hook point)
- `apps/fastapi/services/llm_chain.py` — `pick_synth_deployment_bandit` (chapter-pin selector)
- `apps/fastapi/graphs/knowledge/distiller.py` — `synthesize_chapter` (Phase A/B/A.5/C orchestration + Self-Refine loop)
