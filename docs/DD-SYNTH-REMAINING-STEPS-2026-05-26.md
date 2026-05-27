# Synth Remaining Steps (2026-05-26)

Definitive list of what's left on the Synth pipeline after this session's
speed waves shipped (DD-SYNTH-SPEED-SOTA Wave 1 + Wave A + Wave B). The
pipeline is in a near-final state — only **Wave Q (quality)** is on the
critical path; Wave C is conditional on telemetry; everything else is
deferred or out of scope with documented reasons.

**Cross-references:**
- [`DD-SYNTH-SPEED-SOTA-2026-05-26.md`](./DD-SYNTH-SPEED-SOTA-2026-05-26.md) — the speed audit + 9-ship plan; explains what was just shipped
- [`KD-SYNTH-SOTA-2026-05-24.md`](./KD-SYNTH-SOTA-2026-05-24.md) — earlier quality-focused audit; Wave Q items here are the empirical follow-ups identified after those ships landed
- [`DD-4FRONT-ROADMAP-2026-05-25.md`](./DD-4FRONT-ROADMAP-2026-05-25.md) — 10-bundle roadmap; bundles 5-13 closed in this session
- [`KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md`](./KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md) — classical grader / vault sentinel ship history
- [`SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`](./SYNTH-ARCHITECTURE-SOTA-2026-05-18.md) — pipeline shape

## 1. TL;DR — what to ship next

| Lane | Effort | Ships | Trigger |
|---|---|---|---|
| **Wave Q (Quality)** | **~4.5h** | Q1 + Q2 + Q3 (outline-bounding fixes) | **Ship NEXT** — empirical 0.69 checklist failures on Browser Use + Claude Code chapter 01 demand it. Q4 deferred. |
| Wave C (Deferred speed) | ~6h | C1 only (per-source digest cache) | **Conditional** — only if Wave A telemetry shows digest is still the next biggest hot spot. C2 + C3 likely skip. |
| Beyond Synth | weeks | Lane 3 work | After Wave Q validated. YouTube Content Search / code-org reorg / other distillers. |

After Wave Q, **Synth enters maintenance mode** — new work fires only on
(a) new corpus exposes a new failure mode, (b) May+ 2026 paper supersedes
a current technique, (c) free-tier provider landscape shifts.

## 2. Wave Q — actually ship these (in this order)

### Q1 — Outline section-count cap (~1h, HIGHEST LEVERAGE)

**Where:** `apps/fastapi/domains/dd/synth/outline/constants.py`

**Change:** add a hard upper bound on H2 section count that scales with
corpus size:

```python
def _max_h2_for(n_sources: int) -> int:
    """Hard cap: roughly 1 H2 section per 3 source docs, floored at 3
    and capped at 15. Prevents outline LLM from extrapolating topics
    beyond what the source pool can actually back."""
    return min(15, max(3, n_sources // 3))
```

Wire it into `build_outline_prompt` as a `target_sections_max` directive
AND into `validate_outline_structure` as a hard reject criterion (forces
mgsr_replan loopback when violated).

**Why:** Browser Use produced **41 H2 sections from 38 source documents**
— more sections than docs. The outline LLM extrapolated invented
topics ("Two-factor authentication handling", "CI/CD pipelines",
"Cross-domain navigation") with no source backing. SAWC then dutifully
filled them with recycled boilerplate (27× duplicate import statement
across "different" subtopics). Capping H2 at `n_sources // 3` would
have turned 41 → ~12 sections, eliminating the topical sprawl entirely.

**Empirical evidence:**
- Browser Use ch-01: 38 docs → 41 H2 → 90% duplicate code blocks → 0.69 checklist
- Claude Code ch-01: 42 docs → 12 H2 → 0.69 checklist (less sprawl but
  similar code recycling pattern)

**Expected impact:** Checklist pass rate 0.69 → 0.80+ on small-corpus
chapters. Single hard constant change; no algorithm complexity added.

### Q2 — `code_uniqueness_ratio` checklist criterion (~1.5h)

**Where:** `apps/fastapi/domains/dd/synth/checklist/service.py` (new
deterministic criterion alongside `check_code_density_appropriate`).

**Change:** Add a new criterion that computes:

```python
def check_code_uniqueness_ratio(sawc: dict) -> CriterionResult:
    """FAIL when unique_code_bodies / total_code_blocks < 0.5.

    Catches the SAWC failure mode where the writer recycles the same
    vault snippet across many "different" subtopics — e.g. Browser Use
    ch-01 had 194 code blocks but only 20 unique bodies (90% duplicates).
    """
    bodies = [
        sub.get("code_ref_hash", "")
        for section in sawc.get("sections", [])
        for sub in section.get("subtopics", [])
    ]
    if not bodies:
        return CriterionResult(name="code_uniqueness_ratio",
                               passed=True, kind="deterministic",
                               feedback="no subtopics — vacuously true")
    n_unique = len(set(b for b in bodies if b))
    n_total = len([b for b in bodies if b])
    ratio = n_unique / n_total if n_total else 1.0
    passed = ratio >= 0.5
    feedback = (
        f"code uniqueness {ratio:.0%} ({n_unique} unique / {n_total} "
        f"total); floor is 50% — sections are recycling the same vault "
        f"snippets across different subtopics"
        if not passed else ""
    )
    return CriterionResult(name="code_uniqueness_ratio",
                           passed=passed, kind="deterministic",
                           feedback=feedback)
```

Register it in the criteria list so it runs alongside the 8 existing
deterministic checks.

**Why:** Today's checklist has `code_density_appropriate` (enough code)
and `prose_code_first_not_meta_framing` (code-first prose), but NO gate
on UNIQUENESS. Browser Use ch-01 passed both code-density AND density-
within-bounds yet shipped 90% duplicate snippets. This criterion forces
mgsr_replan to reroll when SAWC over-duplicates — naturally co-operates
with Q1's outline cap (fewer sections → fewer chances to duplicate).

**Expected impact:** mgsr_replan triggers correctly on duplication-heavy
drafts. With Q1 + Q2 together, duplication failures should self-correct
in 1-2 refine iters.

### Q3 — H2 semantic-dedup gate in outline_sdp (~2h)

**Where:** `apps/fastapi/domains/dd/synth/outline/service.py` —
post-parse, pre-DAG-derivation step.

**Change:** After the LLM emits a candidate outline, compute pairwise
cosine similarity between H2 headings (via existing
`nvidia/llama-nemotron-embed-1b-v2` rotator). Merge or reject pairs
above τ=0.85.

```python
async def _dedupe_h2_sections(
    outline: ChapterOutline, threshold: float = 0.85,
) -> tuple[ChapterOutline, list[str]]:
    """Embed each H2 heading; merge near-duplicates pre-SAWC. Returns
    (deduped_outline, list_of_merged_headings_for_telemetry)."""
    headings = [s.heading for s in outline.sections]
    embeddings = await embed_async(headings)
    pairs = []
    for i in range(len(headings)):
        for j in range(i + 1, len(headings)):
            sim = cosine_sim(embeddings[i], embeddings[j])
            if sim >= threshold:
                pairs.append((i, j, sim))
    if not pairs:
        return outline, []
    # Merge transitively (union-find) so {a~b, b~c} collapses to {a,b,c}.
    # Keep the first-listed heading as canonical (preserves outline order).
    ...
```

**Why:** Browser Use ch-01 had near-duplicate H3 subheadings:
- "Click a submit button via CSS selector"
- "Clicking submit button with CSS selector"
- "Click submit button via CSS selector"

These pass exact-string deduplication (already in Pydantic validators)
but are semantically identical. Embedding-cosine merge catches them.
This is FAIL-EARLY: cheaper to fix the outline once than to let SAWC
write 3 nearly-identical sections then fail checklist.

**Expected impact:** Catches the ~5-10% of outlines that survive Q1's
hard cap but still contain semantic dupes. Reuses the existing NIM
embedding rotator (no new infra). Compliant with no-local-inference
rule (`project_local_vs_rotator_architecture`).

### Q4 — Fuzzy atomic-claim grounding (DEFERRED)

**Where:** `apps/fastapi/domains/dd/synth/checklist/faithfulness.py`

**Change:** Replace strict claim-source string match with embedding-sim
based match. ~2h.

**Why deferred:** Both 0.69 failures (Browser Use + Claude Code)
**passed** atomic-claim grounding. This ship targets the next failure
mode, not the current one. Hold until a corpus surfaces an atomic-claim
failure.

## 3. Wave C — Conditional / deferred

Only ship these if Wave A telemetry from the post-ship validation run
identifies the targeted bottleneck as the next biggest hot spot.

### C1 — Digest per-source caching (~6h, CONDITIONAL)

**Where:** `apps/fastapi/domains/dd/synth/digest/node.py` +
`digest/service.py`.

**Change:** Change cache key from per-chapter manifest hash to per-source
hash. Today: one cache blob per chapter (any source-list change
invalidates everything). After: one cache blob per source content hash;
chapter assembly = `gather([read(src.content_hash) for src in chapter.sources])`.

**Why deferred:** Architectural shift (cache key strategy + blob layout +
load logic). Deserves its own focused testing pass. Only worth shipping
if the Wave A response_format speedup didn't already drop digest below
the SAWC bottleneck. Run a fresh chapter after Wave A is validated; if
digest is still ≥20% of chapter wall time, ship C1.

### C2 — book_harmonize cross-chapter pruning (REINVESTIGATE)

**Original premise:** Only harmonize chapter pairs whose topic embeddings
exceed similarity τ.

**Why reinvestigate:** Current `book_harmonize` architecture is **O(N) per
chapter (with batched canonicalize)**, not O(N²) pairwise. The pruning
premise doesn't apply cleanly. Re-read `book_harmonize/service.py` after
Wave A telemetry — if it IS still slow, the bottleneck is per-chapter
detect/patch, not cross-chapter coupling.

### C3 — Pre-warm rotator arms (LIKELY SKIP)

**Marginal:** Only first-call-per-pod has cold TTFT (~200ms-2s). With
Celery prefork workers + `KD_STUDY_SEM=2`, there's at most 2 cold-start
windows per study. Savings ≈ 400ms-4s on a 30+ min study — negligible.

## 4. Out of scope — DROPPED with reasons (do not re-propose)

| Item | Why dropped |
|---|---|
| **CISC critic-replace** | Would undo the pairwise tournament picker (Ship A, May 24, Landesberg 2026 arxiv 2603.12520). Pointwise self-scoring captures only 21% of selection signal on similar-quality long-form drafts — exactly the bug Ship A fixed. |
| **LangGraph speculative execution** | The Synth graph is strictly linear — sawc_write depends on mgsr_replan's memory output. Speculation requires independent parallel branches, which this graph doesn't have. |
| **Heterogeneous role-routing (new rotator instances)** | Already done — the curated dd-synth pool with documented exclusions (Cerebras 404, SambaNova paywall, Groq 70B TPM, Gemini T-floor) is the result of prior role-routing engineering. Re-adding the obvious "free-tier providers" would re-introduce already-debugged failures. |
| **EAGLE-3 / Nemotron-MTP arm additions** | Requires per-endpoint NIM probe to confirm which model IDs expose MTP via the hosted endpoint. Reopen when there's evidence a specific MTP-capable NIM model is hosted at our endpoint. |
| **UCCI calibrated cascade** | FGTS-VA bandit's predict_top_k IS already a cascade-through-top-K design. UCCI's isotonic-regression calibration would duplicate the bandit's variance-aware reward dynamics. Marginal at best. |
| **Adaptive halting head** | Compliance question (`project_local_vs_rotator_architecture`) — logistic regression at runtime is technically in-cluster inference. User declined to ship until the boundary is explicitly drawn. Current Bundle 7 hardcoded threshold suffices in the meantime. |
| **Bandit reward signal for code-density** (task #113, deleted) | Obsolete after v2 cookbook schema (`_SUBTOPICS_MIN=3` + required `code_ref_hash`), Visible Vault, pairwise tournament, sawc_derive, code_density_appropriate checklist gate, and response_format json_schema all shipped. Adds a 7th mechanism to enforce code density when 6 already do it structurally + iteratively. |
| **LLMLingua-2 / prompt compression** | Violates no-local-inference rule (runs distilled BERT in-cluster). Benefit captured by Gemini 2.5 Flash 1M-ctx (no chunking needed) — already in the rotator pool via the existing dd-grader / dd-synth pools. |
| **ThinkPRM replacing checklist** | Quality risk on the SAWC code-accuracy gate. Needs >100 chapter runs of validation before defaulting on. Not worth opening without that empirical basis. |
| **Local cross-encoder rerankers** | No-local-inference rule. Existing NIM-hosted `nvidia/llama-nemotron-rerank-1b-v2` already covers this need. |
| **Paid-tier providers** (Together / OpenRouter / Anthropic) | Free-tier-only constraint. |

## 5. Trigger conditions for future Synth work

Synth is in maintenance mode after Wave Q. New ships only fire on:

1. **A new framework corpus exposes a failure mode not seen on today's 4
   corpora.** Snapshot today's working set: FastMCP (252 docs), LangChain
   (777 docs), Claude Code (42 docs), Browser Use (38 docs). New
   corpora at the boundaries (≤30 docs, ≥1000 docs, non-English,
   non-Python) may surface new patterns. Open a focused ship only if
   the failure is reproducible across ≥2 corpora — single-corpus quirks
   often resolve via the bandit's variance-aware re-classification.

2. **A May+ 2026 paper supersedes a current technique.** Candidates to
   watch:
   - Pairwise tournament picker — any successor to Landesberg / RM
     Knockout Tournament
   - MAMM-Refine / Optimal-Stopping — any successor showing N=1 with
     PRM beats N=2 with critic
   - CoCoA two-stage — any successor that beats 68% F1 on code-doc
     alignment
   - FGTS-VA bandit — any successor that beats variance-aware reward
     dynamics at heterogeneous arm pools

3. **Free-tier provider landscape shifts.** Watch for:
   - New free-tier providers (re-evaluate role-routing pool)
   - Deprecations on current arms (Mistral-Large 3 / Kimi K2.6 EOL)
   - Rate-limit changes that affect bandit cascade depth
   - New speculative-decoding / MTP-capable endpoints on NIM

## 6. Validation plan (run BEFORE Wave Q)

Before shipping Wave Q, run a clean Synth pass on **both** Claude Code
and Browser Use corpora with the just-shipped speed waves. Validate:

**Speed:**
- SAWC `n_drafts_tried` per section: mostly 1 (Optimal-Stopping fired) or 2 (one extra fired)
- Outline `n_samples_drafted`: 1 when sample 1 passes the gate
- `faithfulness_wall_ms` and `cocoa_wall_ms` both smaller than serial baseline
- `*_n_repairs` everywhere should drop sharply
- Total per-chapter wall time: 15-25 min (down from 30-50 min baseline)
- `KD_STUDY_SEM=2` runs 2 chapters concurrent

**Quality:**
- `checklist_stats.pass_rate` must hold ≥0.65 (no regression)
- `code_density_appropriate` must still pass
- Per-arm pick distribution in FGTS-VA logs — pool composition unchanged

**If quality holds ≥0.65 AND ≤0.80:** ship Wave Q to push it to ≥0.80.

**If quality drops below 0.65:** investigate the regression first via
env-flag rollbacks (`KD_STUDY_SEM=1`, `KD_SAWC_OPTIMAL_STOPPING=false`,
`KD_OUTLINE_OPTIMAL_STOPPING=false`, `KD_SAWC_DERIVE_OPTIMAL_STOPPING=false`,
or tighten `_RESPONSE_FORMAT_SAFE_PROVIDERS`). DON'T mix Wave Q
shipping with regression investigation.

**If quality holds ≥0.80:** Wave Q is OPTIONAL — Synth is already at
target. Consider whether the ~4.5h is worth the ~5-10pp headroom on
borderline chapters.

## 7. Status (snapshot 2026-05-26 evening)

**Just shipped (this session):**
- Speed Wave 1: KD_STUDY_SEM=1→2, Optimal-Stopping BoN in SAWC,
  response_format on SAWC writer, latent `_N_DRAFTS=3→2` bug fix
- Speed Wave A: response_format extended to all 8 LLM nodes
  (digest, outline, mgsr, checklist judge, CoCoA, faithfulness,
  sawc_derive reexplain, book_harmonize, SAWC critic-picker)
- Speed Wave B: parallelize CoCoA+faithfulness in checklist,
  Optimal-Stopping on outline candidates, Optimal-Stopping on
  sawc_derive MPSC, SAWC `_MAX_REPAIR_ATTEMPTS` 2→1

**Currently running (validation):**
- Browser Use study `b1f1774d...` (2 chapters, quality mode) — started
  21:19:05. Healthy: bandit picking expected arms (mistral-medium,
  devstral-medium, deepseek-v4-flash, glm-5.1, kimi-k2.6), no
  response_format rejections, no rate limits, no exceptions. Expected
  completion ~21:50-22:00.

**Next action:** Watch the Browser Use validation run complete. Decide
on Wave Q based on the checklist pass rate it produces.
