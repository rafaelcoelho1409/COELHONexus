# Knowledge Distiller — Self-Refine Regression Fixes

Research-backed fixes for the Self-Refine failure mode observed in run `c5de2c9d-dc3c-4e71-94cc-006adea02db7` (2026-04-21): iter 1 scores LOWER than iter 0 on 3 of 5 chapters, compounding drift across iterations, pipeline accepts the *last* (worst) iteration.

Touches: `apps/fastapi/graphs/knowledge/distiller.py` (the refine loop around line 384-460), `apps/fastapi/services/llm_chain.py` (temperature), `apps/fastapi/schemas/knowledge/agents.py` (grader schema).

## Observed failure

| Chapter | iter 0 | iter 1 | Δ |
|---|---|---|---|
| ch01 | 0.73 | 0.71 | **-0.02** (9 issues) |
| ch03 | 0.81 | 0.76 | **-0.05** (10 issues) |
| ch04 | 0.80 | 0.75 | **-0.05** (0 issues) |

**Not a bug — documented behavior.** Intrinsic self-correction without oracle feedback frequently regresses. On GPT-4-Turbo + GSM8K, scores drop from **91.5 → 88.0** after one self-refine round (Huang et al. 2024 ICLR §3.3, arxiv 2310.01798v2). Llama-2 is worse: 62.0 → 43.5 → 36.5.

The core problem: *"the model is more likely to modify a correct answer to an incorrect one than to revise an incorrect answer to a correct one."*

## Authoritative sources

- **Huang et al. 2024 (ICLR, 2310.01798v2)** — regression mechanism + benchmarks showing it's common
- **Kamoi et al. 2024 survey (2406.01297v3)** — "bottleneck is in feedback generation"; intrinsic refinement only works for easy-verification tasks
- **Stechly et al. 2023 (2310.12397)** — GPT-4 on graph-coloring: iterative self-critique HURTS vs one pass
- **Madaan et al. 2023 Self-Refine (2303.17651v2)** — original paper admits it "doesn't work with weaker models"; used T=0.7 for critique (not T=0)
- **Gou et al. 2023 CRITIC (2305.11738)** — span-anchored feedback > issue lists
- **Zheng et al. 2023 MT-Bench (2306.05685)** — documents self-enhancement bias in LLM-as-judge
- **PDR 2510.01123 / Iterative Critique-Refine 2510.24469** — modern consensus: take argmax over iterations, never "last"

## Ranked fixes (highest impact-per-LoC first)

### Fix #1 — Keep-best, not last (HIGH, ~5 LoC)

The single most critical change. Current code accepts the final iteration; regression literally guarantees this is wrong.

**Current (`distiller.py:445-450` area):**
```python
last_eval = history[-1]
if last_eval.weighted_score >= user_profile.acceptance_threshold:
    ...
```

**Fix:**
```python
best = max(history, key=lambda h: h.weighted_score)
if best.weighted_score >= user_profile.acceptance_threshold:
    ...
```

**Impact on this run:** ch01/ch03/ch04 recover their iter-0 scores (0.73/0.81/0.80) instead of worse iter-1 outputs. Immediate +0.02 to +0.05 uplift per regressing chapter.

**Confidence:** HIGH. Every modern iterative-refine paper uses argmax. No downsides.

### Fix #2 — Early-stop on score regression (HIGH, ~3 LoC)

Huang §3.3 shows further rounds compound the "correct→incorrect" drift. Stop iterating once a regression is detected.

```python
for iteration in range(MAX_SELF_REFINE_ITERATIONS):
    # ... existing refine + grade logic ...

    if iteration > 0 and history[iteration].weighted_score < \
            history[iteration - 1].weighted_score - 0.01:
        logger.info(
            f"[synth][ch{n:02d}] regression detected at iter {iteration} "
            f"({history[iteration].weighted_score:.2f} < "
            f"{history[iteration-1].weighted_score:.2f}); stopping early"
        )
        break
```

`ε=0.01` tolerates grader noise. Combined with Fix #1, guaranteed non-regressing output.

**Confidence:** HIGH. Saves API cost + avoids further drift.

### Fix #3 — Refiner temperature = 0.7, grader stays 0 (HIGH, 1 LoC + chain split)

Madaan 2023 explicitly used T=0.7 for the critique/refine step. T=0 collapses exploration — the LLM finds one "fix" path and commits. Grader stays T=0 for determinism (2506.05234 confirms judge-side determinism matters more).

Current: `ChatOpenAI(temperature=0.0)` in `services/llm_chain.py` for all.

**Fix:** add a `build_refine_llm()` factory with T=0.7, used only for revision calls:

```python
def build_refine_llm(groq_timeout_s: int = 120, nim_timeout_s: int = 300):
    """
    Refine-only chain with T=0.7 for exploration during Self-Refine.
    Matches Madaan 2023 (Self-Refine paper) which used T=0.7 for critique.
    T=0 causes the refiner to collapse into a single deterministic edit path,
    which is a known cause of iter N < iter N-1 regression (Huang 2024).
    """
    # Rebuild chain with T=0.7
    ...
```

Route refine calls through this new chain; keep synth+grader on the existing T=0 chain.

**Confidence:** HIGH.

### Fix #4 — Reject-and-regenerate on regression (HIGH, ~20 LoC)

Kamoi §4 "Negative Results": passing the PREVIOUS BAD output to the refiner biases toward the same failure. When regression is detected, discard the failed iter and regenerate from the PRIOR BEST + feedback.

```python
best_so_far = history[0]
for iteration in range(1, MAX_SELF_REFINE_ITERATIONS):
    # Feed the refiner the BEST output so far, not the most recent
    # (which may be worse — accumulating drift problem)
    candidate = synth(
        spec, files,
        base_content=best_so_far.content,
        feedback=best_so_far.issues,
    )
    scored = grade(candidate)
    history.append(scored)

    if scored.weighted_score >= user_profile.acceptance_threshold:
        return scored

    if scored.weighted_score > best_so_far.weighted_score:
        best_so_far = scored
    elif scored.weighted_score < best_so_far.weighted_score - 0.01:
        # Regressed — abandon and retry from the best-so-far
        logger.info(f"[synth][ch{n:02d}] regression — keeping best so far")
        break

return best_so_far
```

Breaks the "refine the broken revision" anti-pattern.

**Confidence:** HIGH. Aligned with Kamoi §4.

### Fix #5 — Span-anchored feedback instead of issues list (HIGH, ~30 LoC)

CRITIC (Gou et al. §3): natural-language feedback grounded in *specific spans* of the output beats free-form critique lists. Current `issues: List[str]` is generic ("missing examples", "unclear flow"). Grader can't tell the refiner WHERE to edit, so it over-corrects globally.

**Change grader schema:**
```python
# schemas/knowledge/agents.py
class Issue(BaseModel):
    span_quote: str = Field(description="Exact text span from the chapter that has the issue")
    dimension: str = Field(description="Which rubric dim (citations, flow, code_density, ...)")
    suggestion: str = Field(description="Specific edit to apply to this span only")

class CriticAssessment(BaseModel):
    ...
    issues: List[Issue]  # was List[str]
```

Refiner prompt then tells LLM: *"For each issue, find the quoted span and apply ONLY the suggested edit. Do not rewrite other parts."*

**Confidence:** HIGH. Known mitigation of Mode 3 (over-correction).

### Fix #6 — Cross-model grader (MEDIUM, ~15 LoC)

MT-Bench (Zheng 2306.05685) documents **self-enhancement bias**: an LLM grading a variant of an output it generated is harsher on variants further from it. Use a DIFFERENT model family for the grader.

You already have Groq + NIM — split them:
- Synthesizer + refiner → NIM reasoning models (glm-5.1, qwen3.5-397b)
- Grader → Groq (llama-4-scout or gpt-oss-120b) — or pin a specific NIM model that's NOT in synth chain

Mitigates judge bias against own-derivative outputs.

**Confidence:** MEDIUM.

### Fix #7 — Dimension-at-a-time refinement (MEDIUM, ~50 LoC)

DECRIM (Ferraz EMNLP-24): decompose multi-constraint refinement into one-constraint-at-a-time passes. Instead of fixing all 8 dims in one revision (Mode 3 over-correction), refine ONLY the lowest-scoring dimension per iteration.

```python
# Find the lowest-scoring dimension
weakest_dim = min(scored.dimensions, key=lambda d: d.score)
# Refine ONLY that dimension
candidate = synth(spec, files, base=best.content,
                  focus_dim=weakest_dim, feedback=weakest_dim.issues)
```

Measured gain on multi-constraint instructions: ~7-12 points over monolithic refine.

**Confidence:** MEDIUM.

### Fix #8 — Multi-sample + select-best (MEDIUM, ~40 LoC, +cost)

RCI (Kim 2303.17491) and PDR (2510.01123): generate N=3 refined candidates per iteration, grade them, keep the max. At matched total compute (3 iters × N=1 vs 1 iter × N=3), Best-of-N typically beats sequential refine.

Higher API cost. Good for quality-critical chapters, overkill for routine.

**Confidence:** MEDIUM.

## Minimum-viable patch (Fixes 1 + 2 + 3, ~10 LoC total)

```python
# graphs/knowledge/distiller.py — refine loop

best = history[0]
for iteration in range(1, MAX_SELF_REFINE_ITERATIONS):
    adjustment = generate_adjustment(best)
    candidate = synthesize(
        spec, files,
        base=best.content,           # not latest — best-so-far
        feedback=best.issues,
        llm=refine_llm,              # T=0.7 chain
    )
    scored = grade(candidate)        # grade_llm stays T=0
    history.append(scored)

    if scored.weighted_score >= user_profile.acceptance_threshold:
        return scored
    if scored.weighted_score < best.weighted_score - 0.01:
        logger.info(f"regression at iter {iteration} — stopping early")
        break
    if scored.weighted_score > best.weighted_score:
        best = scored

return best
```

**Expected impact on current run:** ch01/ch03/ch04 keep iter-0 scores (0.73/0.81/0.80). Iter 2-4 may actually converge upward with T=0.7 exploration instead of T=0 drift.

## What NOT to do

- **Don't raise `MAX_SELF_REFINE_ITERATIONS` beyond 5** — Huang §3.3 shows compounding drift; more iterations make it worse, not better
- **Don't keep `temperature=0.0` on the refiner** — Self-Refine paper used T=0.7 for a reason
- **Don't trust the grader monotonically** — self-enhancement bias is real; cross-model grader or span-anchored issues are the escape

## Priority order

1. **Fix #1** (keep-best) — immediate recovery of observed regression
2. **Fix #2** (early-stop) — saves cost + prevents further drift
3. **Fix #3** (T=0.7 refiner) — enables actual improvement
4. **Fix #4** (reject-and-regenerate) — breaks accumulating-drift pattern
5. **Fix #5** (span-anchored feedback) — better refine signal
6. Fixes #6-8 — incremental quality boosts

Fixes #1-3 are the critical minimum. Implement first; measure; then decide on #4-8.

## Q&A (from research questions)

1. **Regression confirmed in literature?** Yes — Huang §3.3: ~12% of GSM8K samples go correct→incorrect vs ~8% incorrect→correct for GPT-3.5; monotone improvement is NOT typical.
2. **Keep-best trivial fix?** Yes, no downsides. Caching is orthogonal.
3. **Feedback format matters?** Yes — CRITIC §3, span-anchored > issue-list.
4. **Stop on regression?** Yes — Huang §3.3 shows compounding drift.
5. **Multi-sample vs sequential?** PDR 2510.01123 shows at matched compute, best-of-N beats pure sequential.
6. **Pillar-approach works?** Yes — DECRIM EMNLP-24, ~7-12 pts on multi-constraint.
7. **Cross-model helps?** Mainly for self-enhancement bias (Zheng §5).
8. **Judge bias real?** Yes — MT-Bench documents self-enhancement + verbosity bias.
9. **T=0.7 on refiner?** Madaan 2023 used this. Your T=0 is too deterministic.
10. **Reject-and-regenerate on regression?** Aligned with Kamoi §4 — avoid biasing refinement toward prior bad output.
