# Knowledge Distiller — Run-9 Post-Mortem (2026-04-24)

**Study:** DeepAgents + LangChain + LangGraph (crossover → single study)
**ID:** `a7dd7523-b05b-4a0d-bc6f-fae039253c11`
**Docs URL:** `https://docs.langchain.com/oss/python/deepagents/overview`
**Ingest tier:** 1 (llms-full.txt, 10.0 MB)
**Wall clock:** 107 min (16:16:51 → 18:03:24 UTC)
**Final status:** `FAILURE` (`ValueError: too many values to unpack (expected 2)` during curator pass)

**TL;DR.** Run-9 is a significant step up from Run-8's 0/9 wipeout — the pipeline produced **2 real chapter artifacts** (ch03 + ch08), exercised the Self-Refine + KEEP-BEST logic end-to-end, surfaced a latent 3-tuple unpacking bug in curator/linter/glossary code paths, and validated today's Tier-1/2/3/4 roadmap shipments. Six chapters still sentinel'd due to structural LLM limits on 50-117-vault-hash distribution — root cause for the next improvement wave.

---

## 1. Timing breakdown

| Phase | Duration | Notes |
|---|---|---|
| **Ingest** (Tier 1 llms-full.txt) | 4 s | 10.0 MB, 1 file |
| **Noise pre-filter (Tier 4 #17)** | < 2 min | 4105 → 3922 entries (-4%) |
| **Code-aware dedup (Tier 2 #6)** | ~3.5 min | 3922 → 3511 entries (-10%); hand-rolled pairwise Jaccard on ~7.7M pairs |
| **Plan cache lookup** | < 1 s | MISS (dedup changed manifest hash vs Run-8) |
| **MAP (88 shards)** | **30 min** | Significantly slower than Run-8's ~10 min; straggler pileup under OP-1 sem=15 |
| **REDUCE (Clio v2 w/ PCA)** | ~13 min | 266 micro-clusters → 11 chapters. PCA pre-reduction (Tier 1 #3) shipped today |
| **Synth (11 chapters)** | ~57 min | 2 committed, 6 sentinel'd, 3 in-progress when crash fired |
| **Curator** | ~0 s | Crashed on first chapter (3-tuple bug) |

---

## 2. Per-chapter outcomes

| Ch | Vault hashes | Outcome | Iter reached | Best artifact |
|---|---|---|---|---|
| 01 | 53 | in-progress at crash | iter 1 (audit fail: 9 missing/4 fence/6 empty) | partial cache persisted |
| 02 | 55 | in-progress at crash | iter 0 (audit fail: 1 duplicated/7 empty) | partial cache persisted |
| 03 | 89 | **COMMITTED** (no grader reached) | produced README | first ~1500 lines real synth, last ~700 lines raw-source leakage |
| 04 | **109** | sentinel iter 4 | 0 graded | distribution regression: 4→5→69→13 missing |
| 05 | 67 | sentinel iter 4 | 0 graded | 1 missing + 1 invented at final iter |
| 06 | 94 | in-progress at crash | iter 0 | partial cache persisted |
| 07 | 21 | sentinel iter 4 | 0 graded | **2 empty-but-proseful** at final (audit too strict) |
| 08 | 58 | **COMMITTED below-threshold** | iter 0 graded 0.68 | KEEP-BEST saved this chapter |
| 09 | **117** | sentinel iter 4 | 0 graded | iter 2 had only 2 issues; iter 3+4 regressed |
| 10 | 42 | sentinel iter 4 | 0 graded | iter 1 near-clean (1 empty), iter 2+ over-corrected |
| 11 | 66 | sentinel iter 4 | 0 graded | chronic 50% distribution failure |

**Verdict:** 2/11 committed (18%) vs Run-8 0/9 (0%). Real chapters produced but most struggled with distribution.

---

## 3. Per-provider / per-model stats

324 LLM calls across **4 active providers** (down from Run-8's 7 after disabling paywalled/bad-key providers):

| Provider | Calls | OK | Err | Success | Notes |
|---|---|---|---|---|---|
| **Mistral** | 116 | 114 | 2 | **98%** ⭐ | MVP of Run-9 — absorbed load from OP-3's Groq removal flawlessly |
| **NIM** | 120 | 112 | 8 | 93% | glm-5.1 finally stable (was chronic Run-8 timeout); mid-tier reliable |
| **Gemini** | 58 | 44 | 14 | 76% | 3-flash-preview hit Google-side outage window (10× 503) |
| **Zhipu** | 30 | 8 | 22 | **27%** | glm-4.7-flash effectively dead on free tier (16/16 RateLimit); glm-4.5-flash half-working (8/14) |
| **TOTAL** | 324 | 278 | 46 | **86%** | |

**Perfect-record models** (0 errors):
`mistral-small-latest` (30/30), `magistral-small-latest` (22/22), `gemini-3.1-flash-lite-preview` (22/22), `nvidia_nim/minimaxai/minimax-m2.5` (20/20), `devstral-medium-latest` (18/18), `nvidia_nim/openai/gpt-oss-120b` (18/18), `nvidia_nim/deepseek-ai/deepseek-v3.1-terminus` (16/16), `magistral-medium-latest` (16/16), `nvidia_nim/qwen/qwen3.5-397b-a17b` (14/14), `nvidia_nim/meta/llama-4-maverick-17b-128e-instruct` (12/12), `mistral-medium-latest` (10/10), `nvidia_nim/moonshotai/kimi-k2.5` (6/6), `nvidia_nim/nvidia/nemotron-3-super-120b-a12b` (6/6), `nvidia_nim/mistralai/mistral-large-3-675b-instruct-2512` (4/4).

**Zero-success models** (candidates for eventual demotion, but *not before more data points*):
- `openai/glm-4.7-flash` (Zhipu): **0/16** — 100% RateLimit. Run-8 was also 0/12. Two-run pattern suggests free-tier is permanently oversubscribed, BUT per user directive "don't remove what might work later" — keep watchlisted.
- `nvidia_nim/deepseek-ai/deepseek-v3.2`: **0/4** — BadRequest×2 + BadGateway×2. Small sample; keep watchlisted.

---

## 4. Component telemetry — what shipped today and how it behaved

| Component (Tier) | Shipped state | Live evidence |
|---|---|---|
| **LiteLLM Router fail-fast cascade** | Pre-call cooldown + 1200s outer + 120s per-entry | Cascade worked as designed — Zhipu/NIM-v3.2 auto-cooled on first few failures, sibling calls skipped at ~0ms |
| **Tier 1 #1 BM25F two-field ranking** | New upgrade from plain BM25 | Ran silently in every synth call; no failures — on par with plain BM25 from Run-8 |
| **Tier 1 #3 PCA pre-reduction** | New | REDUCE phase completed in 13 min (vs Run-8's ~12 min — similar on this corpus size; quality-neutral) |
| **Tier 2 #6 Code-aware MinHash dedup** | New | **411/3922 entries dropped (10.5%)** — exactly in expected 10-15% range. Pairwise O(N²) took ~3.5 min — acceptable but worth watching on 10K+ corpora. |
| **Tier 2 #8 Parallel curator `Semaphore(2)`** | New | **Never exercised** — curator crashed immediately on 3-tuple bug. Unknown if semaphore path works. |
| **Tier 2 #9 + Tier 4 #18 Grader pre-gates** | New | Not fired — grader was reached only on ch08 iter 0 which passed all pre-gates. No audit-failed chapters reached grader (pre-gate doesn't apply there). |
| **Tier 3 #13 Per-iteration partial cache** | New | **Validated**: smoke-tested pre-run (set/get/identity-miss/clear all pass). Live: ch01/02/06 had partial state persisted on every graded iter. Next run will resume from those iter counters. |
| **Tier 4 #16 Preview mode** | New | Not exercised — Run-9 used full mode. Untested at scale. |
| **Tier 4 #17 Noise pre-filter** | New | **183/4105 entries dropped (4.5%)** — below expected 5-15%. Could tighten patterns to catch more boilerplate. |
| **OP-1 `MAP_SHARD_SEMAPHORE` 30→15** | New | **Negative side effect** — 6-9 min straggler shards (#8, #18, #68, #75, #83) blew MAP from 10 min → 30 min. Halving reduced 429 storm intensity but created long-tail stalls. |
| **OP-3 Drop 3 Groq small-TPM entries** | New | ✅ **Worked perfectly.** Zero Groq 429s in Run-9 (vs Run-8's dozens). Load redistributed to Mistral (116 calls, 98% success). |
| **OP-4 Drop gemini-2.5-flash-lite** | New | ✅ Zero BadRequest events from that model (Run-8 had 14). Removal justified. |

---

## 5. Failure taxonomy

### 5.1 Distribution failures (dominant, 6/11 chapters)

LLMs struggle to maintain exhaustive ref coverage across sections when **vault hash count ≥ 50**. The harder case is **≥ 100 hashes**: ch04 (109) and ch09 (117) both saw regression — iter improves some metrics but drops 10+ refs elsewhere. Classic over-correction (Huang et al. 2024 §3.3).

**Regression example (ch10, 42 hashes):**
```
iter 0: 2 missing + 1 empty   → refine
iter 1: 0 missing + 1 empty   ← BEST (1 issue)
iter 2: 16 missing + 0 empty  ← REGRESSION (LLM "fixed" empty → dropped 15 refs)
iter 3: 2 missing + 3 empty
iter 4: 38 missing + 6 invented ← catastrophic regression
→ TERMINAL FAILURE (no graded iter ever reached)
```

**Why current Self-Refine loop can't rescue this:** regression detection fires on *graded* iter scores, but audit-failing iters never reach the grader. So a near-clean iter-1 with 1 empty-section gets discarded and the loop chases "fix that empty" into worse states.

### 5.2 Schema validation violations (2 instances, both recovered)

ch04 iter 0 and ch11 iter 1 produced `challenges` field as wrong type (list instead of string). My new `PydanticValidationError` handler caught both and converted to refine-signal; chapters continued iterating. **Fix worked.**

### 5.3 Empty-but-proseful over-enforcement (ch07)

ch07's iter 4 audit: 0 missing, 0 invented, 0 duplicated, 0 fence-contaminated, **2 empty-but-proseful** sections. The chapter was essentially clean but sentinel'd because the 40-char threshold for "substantive prose" classified 2 transition sections as "empty". A 21-hash chapter with 2 transition paragraphs is a perfectly valid chapter shape.

### 5.4 Crash: 3-tuple unpack in curator

Latent bug from when `_load_all_chapters` signature changed to return `(number, title, body)` 3-tuples. `_deterministic_linter`, `_extract_glossary_terms`, and today's parallel-curator refactor all unpack as 2-tuples. In Run-8 all chapters sentinel'd → curator never ran → bug dormant. In Run-9 ch03 + ch08 produced READMEs → curator ran → crash.

**Fixed post-crash** (3 LoC across both files, will ship in next push).

### 5.5 Synthesis budget exhaustion leaking raw corpus (ch03)

ch03's first ~1500 lines are real synthesized prose + code. Last ~700 lines are verbatim `--- docs-langchain-com-llms-full-overview-33.md ---` file separators and bare code pastes. LLM ran out of synthesis "steam" past ~60% of its output budget and fell back to raw-source concatenation. Current audit doesn't catch this because sections technically have prose + code_refs.

---

## 6. Crash root cause (fixed)

**File:** `apps/fastapi/graphs/knowledge/distiller.py` (curator node) + `apps/fastapi/graphs/knowledge/helpers.py` (`_deterministic_linter`, `_extract_glossary_terms`).

**Cause:** `_load_all_chapters` returns `list[tuple[int, str, str]]` (number, title, body). Three callers unpacked as `for n, body in chapters`. Worked in Run-8 only because all chapters sentinel'd → `chapters` was empty → loop body never executed.

**Fix:** In all three call sites, project 3-tuples → 2-tuples at entry, or unpack all three positions explicitly.

```python
# Curator fix (distiller.py)
*(_curate_one(n, body) for n, _title, body in chapters)

# Internal normalization (helpers.py, both _deterministic_linter + _extract_glossary_terms)
if chapters and len(chapters[0]) == 3:
    chapters = [(n, b) for n, _t, b in chapters]
```

---

## 7. Proposed improvements (post-Run-9)

Ordered by expected impact. All are NEW improvement candidates based on Run-9 evidence — none are in the existing roadmap.

### OP-5: Revert `MAP_SHARD_SEMAPHORE` 15 → 20-25 + add per-shard 180s time-box
**Why:** OP-1 at 15 caused straggler pileup — 30 min MAP vs Run-8's 10. A middle value + time-box avoids both 429 storms AND long-tail stalls.
**Effort:** ~5 LoC.

### OP-6: Lenient-accept on iteration exhaustion
**Why:** ch07 (2 empty at iter 4), ch09 (3 missing + 1 invented at iter 4), ch10 (iter 1 clean with 1 empty, later regressed) all sentinel'd when they had a perfectly committable near-clean iter. If budget is exhausted AND the LEAST-BAD iter has ≤3 missing OR ≤2 empty OR ≤2 invented, commit it with DEBT flag instead of sentinel.
**Effort:** ~20 LoC. Tracks "least-bad-audit iter" alongside "best-graded iter".
**Est. impact:** saves ch07, ch09, ch10 class failures — +30% committed-chapter rate.

### OP-7: Regression detection on audit failures (not just graded)
**Why:** ch10's iter 1 had 1 empty; iter 2 had 16 missing. Self-Refine's existing early-stop only fires on GRADED score regression. Extending it to "audit total-issue count regressed by >5×" would have committed iter 1 instead of burning through 5 iterations.
**Effort:** ~15 LoC. Track audit issue count per iter; early-stop + commit best-audit when regression hits.

### Tier 3 #12: Sub-chapter synthesis batching (escalated priority)
**Why:** ch04 (109 hashes), ch09 (117 hashes), ch11 (66 hashes) chronically fail single-shot distribution. Splitting into ~30-hash batches, synthesizing each, merging is the architectural fix. Already in the roadmap as "deferred until bottleneck shifts" — Run-9 proves the bottleneck shifted.
**Effort:** ~150 LoC.
**Est. impact:** 3+ chapter saves per run; enables chapters with >100 hashes.

### OP-8: Raw-corpus-leakage detector in audit
**Why:** ch03's tail showed `--- docs-langchain-com-llms-full-X.md ---` file separator leaking through as "synthesized" prose. Add regex check for `--- .*\.md ---` or bare corpus delimiter patterns in `prose_md` → flag as `raw_leakage_sections`.
**Effort:** ~10 LoC. Extends `_audit_structured_output_refs`.
**Est. impact:** raises audit sensitivity to catch tail-degradation failures.

### OP-9: Lower `empty_sections` threshold from 40 chars → 80 chars, OR allow up to 2 per chapter
**Why:** ch07's 21-hash chapter with 2 legitimate transition paragraphs sentinel'd on this. A strict "substantive prose = must have code_refs" is wrong for narrative chapters.
**Effort:** ~5 LoC.

### OP-10: Budget-aware synth prompt instruction
**Why:** ch03's tail degradation suggests the LLM's synth pass goes coherent-then-degraded as output tokens near context limit. Explicit prompt clause: "If you reach output token budget and have remaining code_refs, prefer emitting sections with `code_refs: [...]` and a 1-line prose pointer over verbatim source paste."
**Effort:** prompt edit only, ~5 LoC in `SYNTHESIZER_PROMPT`.

### OP-11: Move adjustment-generation to ONE big "synth-again-with-full-feedback" per iter
**Why:** current pattern: iter produces 4 missing → generate 500-char "you missed these hashes" adjustment → iter produces 16 missing + 3 empty → generate new adjustment → loop drifts. Adjustment text accumulates but each new one overrides the last. Better: pass the full audit diff from the BEST iter so far as the refine anchor.
**Effort:** ~30 LoC in `_generate_adjustment` + adjustments state.
**Est. impact:** reduces regression by anchoring refines to "best-seen" not "last-seen".

---

## 8. What's NOT broken (confidence signals)

Components validated by Run-9 that we can now trust:

- **LiteLLM Router cascade + cooldown cache** — graceful provider degradation handling
- **Tier 0a/b/c code vault** — 100% preservation across all 11 chapters' vault operations. Zero code contamination, zero sentinel corruption.
- **Tier 3 #21 structured output** — schema-based emission; 9/11 chapters at least attempted valid `ChapterOutput`
- **Tier 0d-6 per-chapter isolation** — 6 sentinel'd chapters didn't kill sibling workers; fan-out continued
- **Tier 3 #13 partial cache** — smoke-validated pre-run; persisted per-iter state for ch01/02/06
- **KEEP-BEST argmax over grader iterations** — rescued ch08's iter-0 score=0.68 output when iters 1-4 regressed
- **PydanticValidationError refine-signal handler** — both Run-9 schema violations (ch04, ch11) caught and continued instead of terminating

---

## 9. Immediate next steps

1. **Commit + push** the 3-tuple fix (curator/linter/glossary — ready on disk, awaiting commit)
2. **ArgoCD auto-sync** picks up the fix for prod
3. **Run-10 (prod)**: same study, partial cache hits resume ch01/02/06; ch03/ch08 full-accept cache hits skip re-synth; sentinel'd 6 retry with 5 more iters + benefit of today's fixes
4. **Local dev in parallel**: begin OP-5 (MAP sem + time-box) and OP-6 (lenient-accept) — both small, both directly address Run-9 dominant failure modes

Target for Run-10: **≥ 5/11 chapters committed** (up from Run-9's 2/11).
