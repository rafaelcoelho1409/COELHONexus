# KD Rotator — Next Session Pickup (2026-05-14 → next session)

**Purpose:** action-oriented handoff for the 5 steps that close out the Phase 2 rotator architecture cleanly. Read `docs/KD-SESSION-2026-05-14-FINDINGS.md` first for full context.

**Status at handoff:** Phase 2 + audit-gate fixes shipped (commit `107d73c` + uncommitted local diff). Canary v4 still running with ch02+ch04 hanging on thundering-herd (deepseek-v4-pro). Steps below close the loop.

---

## Step 1 — Kill canary v4 (5 sec)

Architecture validation extracted. The hang is now data, not a runaway expense.

```bash
curl -X DELETE "http://localhost:23020/api/v1/knowledge/studies/ca944e63-28af-4c35-bf24-43e0f1190a9e"
pkill -f canary_monitor.sh 2>/dev/null
```

---

## Step 2 — Commit FastHTML fixes (~5 min)

`apps/fasthtml/components/kd_studies.py` has two unstaged patches (chapter visibility + open-state preservation across HTMX polls). Both verified working.

```bash
git add apps/fasthtml/components/kd_studies.py
git commit -m "$(cat <<'EOF'
FastHTML: KD studies — fix chapter visibility + preserve open state across HTMX polls

- kd_studies.py: ChaptersListFragment now reads tree.get("objects") (was
  reading non-existent "keys"/"files"), so chapters become visible as their
  README lands in MinIO — even mid-study.
- Add JS state preservation via localStorage + htmx:afterSwap handler so
  user-opened <details> cards stay open across the 15s chapter-list poll
  (also re-fires HTMX lazy-load via programmatic .open=true → toggle event).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Step 3 — Commit refiner audit-gate tightenings (~3 min)

`apps/fastapi/graphs/knowledge/distiller.py` has the thin 5→7 + regression 1.5→1.2 bump (canary v3 ch01 evidence-based).

```bash
git add apps/fastapi/graphs/knowledge/distiller.py
git commit -m "$(cat <<'EOF'
FastAPI: KD -> audit gate further relaxed (canary v3 ch01 evidence)

Two empirically-justified bumps in distiller.py:
- _THIN_SECTIONS_ACCEPT_LIMIT 5 → 7 (thin count is structural via A.5
  bucket-split, not refiner-fixable; canary v3 ch01 iter 0 had 6 thin
  which blocked an otherwise-ACCEPT chapter).
- _AUDIT_REGRESSION_FACTOR 1.5 → 1.2 (typical refiner over-correction
  is 1.1-1.5×, not 2×+; canary v3 ch01 iter 0→1 went 13→15 issues at
  1.15× and flew through both old (3) and pass-1 (1.5) thresholds).

Combined effect: canary v3 ch01-shape (2 missing / 5 dup / 6 thin) now
accepts at iter 0 instead of regressing through iter 1, iter 2, ...

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Step 4 — Ship thundering-herd fix (~30-60 min)

**Single biggest remaining architectural issue in the bandit.** Canary v4 evidence: ch02 + ch04 both queried `predict_top_k` within 1 second, both saw `deepseek-v4-pro` at n_obs=0 (highest UCB exploration bonus), neither saw the other's pending pick. Both pinned to it; both hung simultaneously on `<think>` token timeout.

### The fix — atomic Redis reservation in `predict_top_k`

In `apps/fastapi/services/pareto_bandit.py`, modify the `predict_top_k` function:

```python
async def predict_top_k(
    kd_process: str,
    context: np.ndarray,
    candidate_deployments: list[str],
    *,
    redis,
    k: int = 3,
    alpha: float = UCB_ALPHA,
) -> list[tuple[str, float, int]]:
    """Top-K UCB picks with atomic provisional reservation (Phase 3 fix,
    2026-05-14). Canary v4 evidence: parallel pickers both saw the same
    n_obs=0 arm at maximum UCB bonus, both pinned to it, both hung. The
    reservation serializes top-K picks under concurrency.

    Each successful pick claims a short-TTL Redis key
        kd:rotator:pareto:reserved:{kd_process}:{deployment}
    via SET NX EX 60. Concurrent pickers see the claim and skip to the
    next-best arm. Caller releases the reservation in a finally-block
    after the LLM call settles (success OR failure).
    """
    if not candidate_deployments:
        return []

    cells = await asyncio.gather(
        *[get_cell_state(d, kd_process, redis=redis) for d in candidate_deployments]
    )
    scored: list[tuple[str, float, int]] = []
    for deployment, cell in zip(candidate_deployments, cells):
        if cell is None:
            cell = CellState.fresh(deployment, kd_process, benchmark_prior=0.0)
        total, _exploit, _bonus = cell.ucb_score(context, alpha=alpha)
        scored.append((deployment, total, cell.n_obs))
    scored.sort(key=lambda x: (-x[1], x[2], x[0]))

    # NEW — atomic provisional reservation
    RESERVATION_TTL_S = 60   # > typical reasoning-model call latency
    reserved: list[tuple[str, float, int]] = []
    if redis is not None:
        for deployment, ucb, n_obs in scored:
            key = f"kd:rotator:pareto:reserved:{kd_process}:{deployment}"
            try:
                claimed = await redis.set(key, "1", ex=RESERVATION_TTL_S, nx=True)
                if claimed:
                    reserved.append((deployment, ucb, n_obs))
                    if len(reserved) >= k:
                        break
            except Exception as e:
                logger.debug(f"[pareto] reservation failed for {key}: {e}")
                # Fail-soft: take it without reservation (degrades to old behavior)
                reserved.append((deployment, ucb, n_obs))
                if len(reserved) >= k:
                    break
    else:
        reserved = scored[: max(1, k)]

    _record_predict(kd_process)
    if reserved:
        _record_ucb_score(reserved[0][1])
    return reserved


async def release_reservation(deployment: str, kd_process: str, *, redis) -> None:
    """Release the provisional reservation for a (deployment, kd_process)
    pair. Call after the LLM call settles (success OR failure).
    No-op if no reservation existed; safe to call multiple times."""
    if redis is None:
        return
    try:
        await redis.delete(f"kd:rotator:pareto:reserved:{kd_process}:{deployment}")
    except Exception:
        pass
```

### And in the caller (`apps/fastapi/graphs/knowledge/helpers.py`)

Modify the bandit cascade loop in `_invoke_structured_with_fallback` to release reservation after each attempt:

```python
for deployment_id, ucb_score, n_obs in top_k:
    pinned = build_pinned_chain_any(deployment_id, group=target_group)
    if pinned is None:
        await pareto_bandit.release_reservation(
            deployment_id, bandit_kd_process, redis=redis_for_bandit,
        )
        continue
    t0 = time.time()
    try:
        result = await _try_chain(pinned)
        # ... existing reward update ...
        return result
    except Exception as call_err:
        # ... existing reward update ...
        continue
    finally:
        # Release reservation regardless of outcome
        await pareto_bandit.release_reservation(
            deployment_id, bandit_kd_process, redis=redis_for_bandit,
        )
```

### Similarly in `pick_synth_deployment_bandit` (`apps/fastapi/services/llm_chain.py`)

When the bandit picks a chapter-pin, also release the reservation when the chapter completes (or pin some longer TTL so chapter-pinned arms aren't double-claimed). Trade-off: chapter-level pinning intentionally holds an arm for ~10-30 min; the reservation TTL of 60s is too short for that. Easiest fix: bump TTL to 1800 (30 min) for chapter-pin reservations specifically, OR have `pick_synth_deployment_bandit` skip reservation entirely.

Recommended initial implementation: **reservations apply ONLY to per-call top-K cascades** in helpers.py. Chapter-pinning (`pick_synth_deployment_bandit`) skips reservation — the bandit's UCB inertia is enough at chapter granularity (single decision per chapter, not concurrent).

### Test plan

1. Unit test: call `predict_top_k` twice in parallel via `asyncio.gather` — verify they return different `deployment` values for the top pick when arms tie.
2. Integration test: kick off canary v5 with concurrency=3 — verify three different deployments pinned across the first batch.

### Estimated effort

- pareto_bandit.py edits: ~40 LoC (function modification + new helper)
- helpers.py edits: ~10 LoC (try/finally + release call)
- Unit test: ~20 LoC
- Integration validation: 1 canary run

~45 min implementation + validation.

---

## Step 5 — Commit thundering-herd fix

```bash
git add apps/fastapi/services/pareto_bandit.py apps/fastapi/graphs/knowledge/helpers.py
git commit -m "$(cat <<'EOF'
FastAPI: KD -> ParetoBandit thundering-herd fix (atomic reservation in predict_top_k)

Canary v4 evidence: ch02 + ch04 both queried predict_top_k within 1s, both saw
deepseek-v4-pro at n_obs=0 (highest UCB exploration bonus), neither saw the
other's pending pick. Both pinned to it; both hung simultaneously on <think>
token timeout.

Fix: each top-K pick attempts a Redis SET NX EX 60 reservation. Concurrent
callers either claim the next-best arm OR retry up to k attempts. Caller
releases reservation in finally-block after the LLM call settles.

Reservations apply ONLY to per-call top-K cascades (helpers.py); chapter-pin
decisions (pick_synth_deployment_bandit) skip reservation because chapter
granularity has natural inertia (one decision per chapter, not concurrent).

Eliminates the parallel-cold-arm collision while preserving the bandit's
exploration semantics — short TTL (60s) just serializes the pick decision
under concurrency without affecting the underlying UCB dynamics.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## What this leaves the project with

After step 5 ships:

| State | Status |
|---|---|
| Phase 1 dynamic catalog | ✅ shipped (commit `107d73c`) |
| Phase 2 ParetoBandit (warm-start, top-K cascade, ADWIN drift) | ✅ shipped (commit `107d73c`) |
| Bandit-driven chapter pinning | ✅ shipped (commit `107d73c`) |
| Refiner early-stop (thin 7, regression 1.2×) | ⏳ Step 3 |
| FastHTML chapter visibility + open-state | ⏳ Step 2 |
| Thundering-herd fix (atomic reservation) | ⏳ Step 4-5 |
| Showcase prep (README, demo, blog) | future session |

The rotator architecture is then **fully production-ready** for the showcase phase.

---

## Minimum-viable handoff (if next session is short on time)

Steps 1-3 are cheap and deterministic (~10 min total). They get the existing uncommitted work shipped — that's the minimum-viable handoff. Step 4 is the architecturally important one but isn't blocking (the bandit still works without it for sequential or warm-arm-parallel runs).

**Recommended order if time-constrained:**
- Always do steps 1-3 (10 min)
- Do step 4 if you have 45+ min available
- Otherwise defer 4-5 to a later session; the issue is well-documented in this doc

---

## Architecture notes for thundering-herd fix

Why Redis SET NX EX is the right primitive:
- **Atomic** — only one client succeeds per key per TTL window
- **Distributed** — works across Celery worker processes (multiple forks query the same Redis)
- **Fail-soft** — Redis unavailable → fall through to old behavior (still works, just no protection)
- **No coordination required** — no consensus protocol, no leader election
- **Short TTL keeps the system self-healing** — if a worker crashes mid-call, the reservation expires within 60s

Why NOT in-process locks:
- Celery prefork workers each have their own process; in-process locks don't cross processes
- The bandit needs cross-process coordination

Why NOT alternatives like Redis SETNX without TTL:
- Workers can crash and leave dangling reservations forever

---

## Cross-references

- `docs/KD-SESSION-2026-05-14-FINDINGS.md` — full session findings, including the thundering-herd identification
- `docs/KD-ROTATOR-PARETO-BANDIT-DECISION-MAY2026.md` — ParetoBandit vs PILOT decision
- `docs/KD-ROTATOR-ALWAYS-ON-BANDIT-MAY2026.md` — always-on architecture playbook (this fix is the natural extension)
- `services/pareto_bandit.py:predict_top_k` — the function to modify in step 4
- `graphs/knowledge/helpers.py:_invoke_structured_with_fallback` — bandit cascade call site
