# KD LLM-Only Mode — Failure Map (2026-05-16)

**Subject:** comprehensive failure inventory from the first end-to-end **LLM-only** LiteLLM study after all `KD_USE_CLASSICAL_*` env flags were flipped to `0`. The ParetoBandit + improved rotator infrastructure was active; classical scaffolding (MAP, outline, grader, summary, refiner, curator, critic) was disabled.

**Study**: `f744c829-f931-4e67-aecd-5a7cf88e6718` (`llm-only-v1/knowledge/litellm-latest-senior`), kicked off 2026-05-16 13:46:14 UTC.

**Companion docs**: `KD-PLANNER-ANALYSIS-2026-05-15.md` (v3 classical baseline), `KD-CANARY-V7-V10-FINDINGS-2026-05-14.md` (architecture state pre-flip), `KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md` (sub-step taxonomy).

**Headline finding**: the run completed every chapter end-to-end (no Celery 2h timeout — improvement over canary v1-v3) but **0 of 9 chapters reached the 0.85 grader-accept threshold**. All chapters shipped as **DEBT** flagged for the Assembler. Two distinct failure classes dominate, both with concrete fixes.

---

## Per-step failure inventory

### Step 2.7 — MAP shard labeling

| Metric | Value |
|---|---|
| Shards | 13 |
| Successfully labeled (strict-JSON OK) | 4 (shards 4, 12, 13, +1 more) |
| Strict-JSON validation error → fell to function_calling | 3 (shards 5, 8, 9) |
| **Timed out at 180s timebox → synthetic cluster** | **8 (shards 1, 2, 3, 5, 6, 7, 10, 11)** |
| NotFoundError 404 (Nemotron 51B function UUID stale) | 1 (shard 9 — failed on both strict + fallback) |

**Root cause**: NIM deployments enter `<think>` token reasoning on long structured-output prompts and produce no output for >180 s. The LLM-MAP path uses `llm.bind(response_format=...)` directly via LiteLLM Router with `simple-shuffle` — **no bandit learning to avoid hanging deployments**. The 180 s timebox catches each hang individually but emits a synthetic cluster, losing semantic signal for that shard.

**Cascade effect**: synthetic cluster names ("Shard 3 (timed out)") propagated into REDUCE meta-labeling. **Ch3 was named `'Shard 3 (timed out) and Related'` with title-coherence 0.275** — well below 0.35 RED threshold (logged via Ch02 detector).

**Fix candidates ranked**:

| # | Fix | Effort | Expected gain |
|---|---|---|---|
| 1 | **Wire bandit into `_label_shard`** (`distiller.py:325-454`) — refactor strict-JSON-Schema path to use `_invoke_structured_with_fallback`-equivalent with reward updates | 4-6 h | Bandit converges to non-hanging deployments after ~3-5 shards; timeout rate drops from 62 % → ~10-20 % |
| 2 | **Tighten timebox to 90 s** | 5 min | Shorter wait per stuck shard; cumulative wall-clock saving |
| 3 | **Filter `<think>`-prone deployments from kd-all pool at discovery** | 1 h | Permanent removal of known-bad arms; complementary to (1) |
| 4 | **Pre-warm REDUCE re-cluster on synthetic clusters** | 1 h | Synthetic-named clusters get re-labeled before meta-label sees them, avoiding the "Shard N (timed out)" chapter title leak |

---

### Step 2.10 — REDUCE meta-labeling

| Metric | Value |
|---|---|
| Meta-clusters produced after k-means + thin-merge + split | 10 |
| Meta-label LLM calls | 10 |
| Successful labels | 9 |
| **Failed with `OutputParserException: Invalid json output`** | **1 (meta 6)** |
| Hedged-invoke fanout=2 saved the rest | yes |

**Root cause**: the LLM produced malformed JSON despite the `json_schema` mode constraint. The hedged-fanout=2 caught most cases (parallel race, first valid wins) but for meta 6 both racers failed. Synthetic draft fell back: title = `<seed_cluster_name> and Related`.

**Fix candidates ranked**:

| # | Fix | Effort | Expected gain |
|---|---|---|---|
| 1 | **Lift fanout from 2 → 3** in `reduce_cluster.py:_hedged_invoke` callers (line 759 + line 908) | 5 min | 3 parallel races; probability of all-fail drops from ~2 % → ~0.2 % |
| 2 | **JSON-repair pass before validation** — try `json5` / `jsonrepair` on failed responses before retrying | 30 min | Catches "almost-valid" JSON like trailing commas or unclosed strings |
| 3 | **Wire bandit into meta-label** | 1-2 h | Learn which deployments produce bad JSON; deprioritize. Lower priority since hedged-invoke already mitigates |

---

### Step 2.10b — Chapter title cascade from MAP failures

| Issue | Value |
|---|---|
| Chapters titled after timed-out MAP shards | 1 (Ch3 `'Shard 3 (timed out) and Related'`) |
| Title coherence on that chapter | **0.275** (RED, below 0.35 threshold) |

**Root cause**: REDUCE meta-label LLM takes a seed cluster name as input. When the seed is a synthetic timed-out cluster, the meta-label inherits the junk wording.

**Fix candidates ranked**:

| # | Fix | Effort | Expected gain |
|---|---|---|---|
| 1 | **Strip `(timed out)`, `(unlabeled)`, `(parse-fallback)` from cluster names in REDUCE input** | 15 min | Meta-label gets clean input; produces real chapter title from the assigned-files content |
| 2 | **Re-prompt meta-label with low-coherence chapter title** — if coherence < 0.40 post-emission, re-call META_LABEL_PROMPT with a "name the SPECIFIC technical topic" hint | 1 h | Surgical correction; only fires on flagged chapters |

---

### Step 4 — Phase C chapter synthesis (the largest defect class)

**9 chapters, all DEBT-flagged**:

| Chapter | Pinned model | Grader score | Iters | Audit (final iter) | Outcome |
|---|---|---|---|---|---|
| ch01 | qwen3.5-397b-a17b | **0.00 (rescue)** | 0 graded | 0 missing / 0 invented / 1 duplicated / 10 empty-but-proseful / 3 thin | OP-12 RESCUE → DEBT |
| ch02 | deepseek-v4-pro (round-robin after NIM saturation) | **0.00 (rescue)** | 0 graded | 0 missing / 2 invented / 6 duplicated / 1 empty / 7 thin | OP-12 RESCUE → DEBT |
| ch03 | (round-robin) | 0.15 | 1 | thin | DEBT |
| ch04 | (round-robin) | **0.00 (rescue)** | 0 graded | 7 missing / 0 invented / 5 empty | OP-12 RESCUE → DEBT |
| ch05 | qwen3-next-80b-a3b-instruct | 0.68 | 1 | minor thin | DEBT |
| ch06 | (round-robin) | 0.68 | 1 | minor thin | DEBT |
| ch07 | (round-robin) | **0.00 (rescue)** | 0 graded | 3 empty | OP-12 RESCUE → DEBT |
| ch08 | qwen3.5-397b-a17b | **0.00 (rescue)** | 0 graded | 0 missing / 0 invented / 0 empty / 2 minor | OP-12 RESCUE → DEBT |
| ch09 | qwen3-next-80b-a3b-instruct | 0.19 | 1 | thin | DEBT |

**Two intertwined root causes**:

**(a) Audit failures dominated by `empty-but-proseful` and `thin/zero-citation`**:
- Pattern: LLM produces prose paragraphs but doesn't include code-ref hashes in the structured output `code_refs[]` field
- The audit catches it: section has prose but zero citations → marked thin
- Self-Refine fires forced-regenerate, but next iter has similar problem
- Free-tier NIM deployments inconsistently respect the `code_refs` field in the schema

**(b) Grader call failures** (causing OP-12 RESCUE → DEBT without graded score):
- Pattern: synthesizer produces output, audit runs, then grader LLM call hangs/errors before returning
- 5 of 9 chapters never received a graded iteration
- Grader uses `_invoke_structured_with_fallback` (bandit-driven) but free-tier deployment quality on long evaluation prompts is poor
- OP-12 RESCUE is doing its safety-net job — but a DEBT-flagged chapter is what ships

**Bandit-pin behavior observed**:
- ch01, ch05, ch08, ch09: bandit picked specific deployments cleanly (UCB scores reported)
- ch02, ch03, ch04, ch06, ch07: bandit saw all top-5 NIM slots saturated → fell through to round-robin (provider semaphore set to 2 chapters max per provider)
- The provider-aware reservation prevented thundering-herd on NIM but forced 5 chapters into less-optimal deployments

**Fix candidates ranked**:

| # | Fix | Effort | Expected gain |
|---|---|---|---|
| 1 | **Stronger prompt schema enforcement** — add explicit `"code_refs": ["hash1", "hash2", ...]` example in the SECTION_SYNTH_PROMPT system message; cite specific schema field rules | 30 min | Drops `empty-but-proseful` count; LLM emits code_refs when shown the exact shape it must produce |
| 2 | **Few-shot examples in SECTION_SYNTH_PROMPT** — show 1-2 examples of well-formed Section with code_refs populated | 1 h | Free-tier deployments anchor better with concrete examples |
| 3 | **Grader timeout shorter + bandit-aware reward update on grader hangs** — currently grader hangs eat 60-180 s per failure; if we cut to 30 s + record negative reward, bandit learns to avoid grader-bad deployments | 1-2 h | Fewer rescue-only iterations → more chapters get graded |
| 4 | **Lift provider semaphore from 2 → 3 chapters/provider on small-corpus runs** | 5 min | Bandit pin succeeds for more chapters; fewer round-robin fall-throughs |
| 5 | **Replace LLM grader with classical structural grader** (count missing/invented/empty deterministically — no LLM call needed for the structural-quality dimension) | 2-3 h | Eliminates grader-call failures entirely; grader becomes 100 % reliable |

---

### Step 5/6/7 — Curator / Critic / Assembler

**Status at writing**: run is in `phase=synthesize`, `last_node=synthesize_chapter`, `nodes_seen=10`. Curator just started (`[curator][ch07] normalized (4339B → 2798B, 3 code blocks preserved)`).

These three nodes have **never executed end-to-end in any prior production run** (canary v7-v10 all hit Celery 2h soft-limit or were cancelled before reaching them). This run will be the first.

**Will update this section once the run reaches SUCCESS/FAILURE.**

---

## Aggregated error counts across the run

```
RuntimeError                6
RESOURCE_EXHAUSTED (429)    6
NotFoundError (404)         6  (NIM Nemotron 51B function UUID stale)
OutputParserException       4
ValidationError             1  (Pydantic strict-JSON parse)
TOTAL                      23
```

Plus the per-shard logging of 8 timeouts as `warnings`. Roughly 31 distinct error events across the run, all absorbed by the rotator/audit/rescue safety net — pipeline still completed end-to-end.

---

## Comparison to v3 classical baseline (apples-to-apples)

| Dimension | v3 (classical + bandit-PhaseC) | This run (LLM-only + bandit-PhaseC) |
|---|---|---|
| Chapters above 0.85 grader threshold | 0 / 8 | 0 / 9 |
| Chapters reaching DEBT-flag stage | 8 / 8 | 9 / 9 |
| Mean chapter coherence (computed) | 0.388 | TBD (pending coherence diagnostic on final output) |
| MAP step duration | ~50-80 s (classical) | 181 s (LLM-MAP, all shards bounded by timebox) |
| Pipeline reached curator/critic/assembler | no (hit Celery 2h) | **yes** |
| Total wall-clock | 2 h timeout | ~1 h, still running |
| Chapters with grader-validated score | mostly graded | only 4 / 9 graded (others rescue-committed) |

**Honest read**: LLM-only is **comparable in chapter quality** to classical baseline (both produce DEBT-flagged chapters with audit defects) but **trades grader signal coverage for wall-clock progression**. The classical-MAP path has structurally fewer failure modes (no `<think>` hangs) but slower per-step.

---

## Top 10 ranked fixes by impact-per-effort

| # | Step | Fix | Effort | Impact |
|---|---|---|---|---|
| 1 | 4 — Phase C | **Stronger SECTION_SYNTH_PROMPT** — explicit `code_refs` field example | 30 min | High — drops empty-but-proseful audit failures |
| 2 | 4 — Phase C | **Few-shot Section examples** in prompt | 1 h | High — anchors free-tier deployments |
| 3 | 2.10b | **Strip `(timed out)` / `(unlabeled)` from cluster names before meta-label** | 15 min | Eliminates Ch3-class title leakage |
| 4 | 4 — Phase C | **Replace LLM grader with classical structural grader** | 2-3 h | Eliminates grader-failure DEBT class entirely |
| 5 | 2.7 — MAP | **Wire bandit into `_label_shard`** | 4-6 h | Cuts MAP timeout rate from 62 % → ~15 % |
| 6 | 4 — Phase C | **Lift provider semaphore 2 → 3** for small-corpus | 5 min | More chapters get bandit-optimal pin |
| 7 | 2.10 | **Hedged-invoke fanout 2 → 3** for meta-label | 5 min | Drops meta-label all-fail rate from ~2 % → ~0.2 % |
| 8 | 2.7 — MAP | Tighten timebox 180 s → 90 s | 5 min | Cumulative wall-clock saving |
| 9 | 4 — Phase C | Shorter grader timeout + sync reward | 1-2 h | Cuts grader-hang wall-clock |
| 10 | 2.7 — MAP | Filter `<think>`-prone deployments at discovery | 1 h | Permanent removal of known-bad arms |

---

## Recommended sequencing

**Tonight** (cheap + high-leverage, ~1 h total):
- Fix #1 + #2 + #3 + #6 + #7 + #8

**Next session** (2-3 h):
- Fix #4 (classical structural grader replaces failing LLM grader)

**Following session** (4-6 h):
- Fix #5 (wire bandit into LLM-MAP)

**Post-fix re-run**: same LLM-only env config, same LiteLLM corpus, compare chapter outcomes against this baseline (9 DEBT, 4 graded).

---

## Notes on the iteration story for portfolio

This run's chapter outcomes are objectively poor (9 DEBT) but the **failure-mode coverage is excellent**:

- Bandit infrastructure validated (chapter-pin firing, provider-aware reservation working, ADWIN drift detection active)
- Audit gates catching real issues (empty-but-proseful, thin sections, duplicated refs, missing hashes)
- OP-12 RESCUE safety net working (no chapter lost despite grader failures)
- Curator/critic/assembler reached for the first time in production

The senior-engineer pitch: *"I built the rotator + audit + rescue infrastructure to handle the failure modes I'd see in LLM-only mode. Tested it; identified 10 specific defects across 8 sub-steps with measured failure rates. Fixed the top-6 in ~1 hour; the remaining 4 in subsequent sessions. Final quality: [post-fix numbers]."*

This is the document the README links to.

---

## Cross-references

- `apps/fastapi/graphs/knowledge/distiller.py:325` — `_label_shard` (MAP, NOT bandit)
- `apps/fastapi/graphs/knowledge/distiller.py:325-454` — MAP strict-JSON + fallback
- `apps/fastapi/graphs/knowledge/reduce_cluster.py:759` — meta-label hedged invoke
- `apps/fastapi/graphs/knowledge/reduce_cluster.py:908` — chapter ordering hedged invoke
- `apps/fastapi/graphs/knowledge/helpers.py:1790` — `_invoke_structured_with_fallback` (bandit-aware)
- `apps/fastapi/graphs/knowledge/helpers.py:2547, 2593, 2809` — grader/critic/curator wrappers
- `apps/fastapi/graphs/knowledge/hierarchical_synth.py:142` — Phase A outline (bandit-driven)
- `apps/fastapi/graphs/knowledge/hierarchical_synth.py:668` — Phase C section synth (bandit-driven)
- `apps/fastapi/schemas/knowledge/prompts.py` — prompt templates (target for fix #1, #2)
- `apps/fastapi/services/pareto_bandit.py` — bandit infrastructure (working as designed)

---

## Live state at doc-write time

```
task_state: PROGRESS
phase: synthesize
last_node: synthesize_chapter
nodes_seen: 10
chapters_done: 9 / 9 (all DEBT-flagged)
[curator][ch07] normalized — curator running
```

Will update this doc with curator/critic/assembler outcomes when the run reaches terminal state.
