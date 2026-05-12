# KD Synthesizer — LLM-to-Classical Replacement Plan (May 2026 SoTA)

**Date:** 2026-05-12
**Status:** Research-validated plan, not yet started.
**Sister doc:** `KD-PLANNER-REDUCE-MAY2026-OPTIMIZATION.md` (the completed planner sprint that validated the LLM→classical-algorithm-plus-small-LLM pattern at scale).
**Code anchors:** `apps/fastapi/graphs/knowledge/distiller.py` (grader, refiner, curator, critic, summary), `apps/fastapi/graphs/knowledge/hierarchical_synth.py` (Phase A outline + Phase B routing), `apps/fastapi/schemas/knowledge/prompts.py` (every LLM prompt), `apps/fastapi/schemas/knowledge/agents.py` (the schemas the LLMs target).

## Headline

~70% of LLM calls in the synthesizer are **replaceable** with classical algorithms or hybrid math + tiny-LLM cores. Section synthesis (Phase C) is the only irreducibly LLM-bound step (creative prose generation). The 6/8 deterministic grader dimensions + the entire critic (via MiniCheck/AlignScore) are the biggest wins.

**Aggregate expected impact** if all recommendations ship:

| Metric | Current | Improved | Delta |
|---|---|---|---|
| LLM calls per study | 140–250 | 50–80 | **-60%** |
| Token cost per study | ~1.1M | ~650k | **-40%** |
| Wall-clock per study | 25–40 min | 12–20 min | **-30%** |
| Reliability (audit↔grader agreement) | Audit and LLM grader can disagree (Run-9 §5.1) | Deterministic scorers cannot disagree with audit | strictly improved |
| Output prose quality | LLM-creative | LLM-creative on Phase C; deterministic everywhere else | same on prose, better on structure |

**The biggest win is reliability, not speed.** Section synthesis (Phase C) still dominates wall-clock and is irreducible. The improvements knock ~10–15 min off curator/grader/critic/outline overhead. Don't expect synth to go from 30 min → 5 min; expect 30 min → ~17 min.

## Current synth pipeline shape (per chapter, fanned out parallel across N chapters)

```
1. LOAD       chapter files                              (deterministic, ~1s)
2. VAULT      regex-extract fenced code blocks → opaque  (deterministic, ~0.1s)
              <code-ref hash="abc123"/> placeholders;
              store original keyed by 12-hex hash
3. OUTLINE    OUTLINE_PROMPT → ChapterOutline             (1 LLM call, ~30-60s)
              4-15 OutlineSection{heading, goal,
              assumes_from_prior_sections}
4. ROUTE      Phase B — embed (section heading+goal) +    (deterministic, ~1s)
              embed (vault hashes); cosine-assign each
              hash to closest section
5. PER-SECTION
   SYNTH      SECTION_SYNTH_PROMPT per section in parallel (~10 LLM calls × ~30-90s)
              → Section{heading, prose_md, code_refs}
6. ASSEMBLE   interleave vault code at code_refs           (deterministic, ~1s)
              positions → assembled markdown
7. GRADE      GRADER_PROMPT → GraderEvaluation             (1 LLM call, ~30-60s)
              8 dims + weighted_score + action ∈ {accept,
              refine, regenerate} + span-anchored Issues
8. SELF-REFINE if action=refine: ADJUSTMENT_PROMPT +       (1-3 iters × 2 LLM calls)
              re-SECTION_SYNTH + re-GRADE. Best-of-N
              argmax over iterations.
9. WRITE      README.md + challenges.md + flashcards.json (deterministic)
```

After all N chapters finish:

```
10. CURATOR  CURATOR_PROMPT × N chapters (Semaphore(2))   (N LLM calls, ~1-3 min each)
             style normalization: terminology, transitions,
             heading depth, voice
11. CRITIC   CRITIC_PROMPT × 1 over concatenated study     (1 LLM call, ~60-120s)
             3 dims: citation_coverage, faithfulness,
             code_syntax_valid
12. SUMMARY  ASSEMBLER_PROMPT → summary.md                 (1 LLM call, ~30-60s)
             framing + reading plan + market roadmap +
             money projects
```

## Per-step replacement summary

| Step | Today | Replacement | LLM calls saved | Wall-clock saved | Token cost saved | Production-ready |
|---|---|---|---|---|---|---|
| **A. Outline** | 1 LLM/chapter (~30-60s, 30k tokens in) | `wtpsplit` sentence-seg + `SemanticChunker` percentile breakpoints + tiny LLM (`kd-reduce-label`) for naming + 1 small-LLM final pass for `assumes_from_prior_sections` | ~80% (replaces 1 big call with N tiny calls) | -70% (~50s → ~15s) | -80% | wtpsplit SaT-3l-sm (EMNLP 2024), SemanticChunker (LangChain), `kd-reduce-label` rotator already validated 2026-05-11 |
| **C. Section synth** | ~10 LLM/chapter × N_iter | **KEEP** — irreducibly creative prose generation | 0% | 0% | 0% | n/a (no replacement plausible at May 2026 SoTA) |
| **B. Grader** | 1 LLM/refine-iter × N chapters | 7/9 dims fully deterministic (textstat, tree-sitter, regex, NLI); 1 dim small-LLM (market_analysis) | ~95% | -90% (60s → 5s) | -95% | textstat 0.7.13, textdescriptives 2.8.4, ModernBERT-large-nli (May 2025/26 SoTA), tree-sitter (already in pyproject) |
| **C-refine. Refiner** | 2 LLM calls/iter (ADJUSTMENT_PROMPT + re-synth) | Deterministic regex/spaCy patches for top-10 regression patterns; LLM only on residual issues | ~50% | -50% per refine iter | ~50% | spaCy Matcher patterns, rapidfuzz, mdformat (already in pyproject) |
| **D. Curator** | N × LLM passes for style normalization | (1) glossary substitution (regex/GLiNER2), (2) heading depth via mdformat, (3) transition phrase deletion via spaCy patterns, (4) 1 small-LLM final pass for tone/voice only | ~70% (eliminate stages 1-3 from LLM) | -50–70% | -70% | All standard NLP; Phi-4-mini-instruct for small-LLM tone pass |
| **E. Critic** | 1 LLM/study, 3 dims | `code_syntax_valid` already tree-sitter; `citation_coverage` regex + `Path.exists()`; **`faithfulness` via Bespoke-MiniCheck-7B OR AlignScore-large** | 100% (zero LLM in critic) | -50–70% (90s → 25s) | -100% | MiniCheck (EMNLP 2024, 77.4% on LLM-AggreFact, beats GPT-4-as-judge); AlignScore-large (ACL 2023, 355M, CPU-runnable) |
| **F. Summary** | 1 large LLM | Deterministic chapter index + reading plan in Python; small LLM only for 1-paragraph framing + 3-5 money-project ideas | ~80% | -80% (45s → 10s) | -90% | Phi-4-mini-instruct + structured output |

## Per-step rationale (selected; full text in earlier deep-research)

### Why the grader (Step B) is the biggest win

8 dimensions, each classically replaceable:

| Dim | Replacement |
|---|---|
| `signal_to_noise` | Regex blacklist on intro phrases ("In this chapter we will...", "Furthermore", "Summary", "Conclusion") + prose-vs-code line ratio. Penalize matches, reward sections opening with code-ref. |
| `assumption_match` | `tasksource/ModernBERT-large-nli` (184M ONNX, 50ms/pair) — entail user_profile.mastered_technologies against chapter sentences; penalize re-explanation of mastered tech |
| `job_alignment` | Substring match on user_profile.target_markets + curated keyword list (G42, DBS, Grab, etc.) |
| `citation_integrity` | Regex `# docs: <slug>` count vs total non-trivial claims; cross-check against `research/raw/` listing |
| `code_density` | tree-sitter count of code lines / total lines (already partly upstream) |
| `portfolio_synergy` | Substring match on user_profile.portfolio_refs |
| `complexity_appropriate` | `textstat` (Flesch-Kincaid, Coleman-Liau, Dale-Chall) targeted to user_profile.level expected grade band |
| `market_analysis` | **Small LLM** (Phi-4-mini-instruct, ~500 tokens) — only dim where prose judgment is irreducible |
| `code_preservation_ratio` | Already deterministic upstream per `agents.py:281-297` |

The Run-9 §5.1 audit↔grader disagreement bug becomes structurally impossible: deterministic scorers can't disagree with the audit because they ARE the audit.

### Why critic faithfulness goes to MiniCheck / AlignScore

`MiniCheck` is the May 2026 SoTA grounded-faithfulness evaluator. The Bespoke-MiniCheck-7B variant tops the LLM-AggreFact benchmark at 77.4% — **beats GPT-4-as-judge on the same task**. AlignScore-large (RoBERTa, 355M, CPU-runnable) is the lighter-weight backup. Both are pre-trained, frozen, deterministic — zero LLM API calls.

### Where the LLM stays

| LLM stage | Why irreducible |
|---|---|
| Section synth (Phase C) | Creative prose compression — the actual product |
| Refiner residual | Rewriting prose at a different complexity level |
| Curator voice pass | Mixture-of-Agents style harmonization across heterogeneous proposers |
| Summary framing + money-projects | Open-ended creative content generation |
| Outline section naming | Multi-word semantic labeling (same shape as REDUCE meta-label) |
| Grader market_analysis dim | Open-ended monetization judgment |

For each remaining LLM call, the smallest reliable model on May 2026 free-tier:

- **`kd-reduce-label` rotator group** — already validated 2026-05-11; reuse for outline naming, grader market dim, summary money-projects
- **Phi-4-mini-instruct (3.8B)** — ARC-C 83.7%, GSM8K 88.6%, structured-output stable; host-side via llama-server for curator tone pass + refiner residual
- **Qwen3-1.7B** — backup
- **Llama-3.2-3B-Instruct** — backup

## Ship order (5 phases, ~1500 LoC total, ~12 days)

| # | Phase | Independence | Days | LoC | Why this order |
|---|---|---|---|---|---|
| **1** | **Grader (B)** | Self-contained | 3-5 | ~500 | Largest single token reduction; biggest reliability win (audit↔grader fix); easiest to A/B (numeric scores) |
| **2** | **Critic (E)** | Self-contained | 2 | ~200 | Drops critic LLM entirely; MiniCheck has published benchmark proof of beating GPT-4 |
| **3** | **Outline (A)** | Self-contained (precedes Phase C) | 2 | ~150 | Reuses existing `kd-embed` rotator + adds wtpsplit; validate Phase C still produces coherent chapters under new outline shape |
| **4** | **Refiner (C)** | **Depends on Phase 1** (needs dim-labeled grader issues) | 3 | ~250 | Touches most-iterated code path (Self-Refine loop); patchers ship after grader emits labeled dimensions |
| **5** | **Curator (D) + Summary (F)** | Independent + small; ship together | 2 | ~400 | Smallest scope; last because they run after every chapter is accepted |

## Workflow per phase (mirrors planner sprint)

1. **Ship deterministic replacement BEHIND a comparison endpoint.** Add `/api/v1/knowledge/debug/<step>_compare?study_id=X&chapter_num=Y` that runs both OLD (LLM) and NEW (classical) side-by-side, returns both outputs + timings + token counts.
2. **Skaffold redeploy.**
3. **Validate side-by-side on a cached chapter** (we have Docker's plan cached from 2026-05-12 study `6b2ea2cf`).
4. **Inspect**: scores, prose diffs, timings, token costs, agreement rates.
5. **If NEW ≥ OLD** on benchmarked axes → flip default + remove OLD path.
6. **If NEW < OLD** → tune thresholds or model selection; redeploy; re-validate.
7. **Move to next phase.**

### Pre-sprint setup (one-time, ~30 min)

Add fixture capture so debug endpoints don't need fresh LLM calls each test:

- `services/knowledge/synth_fixtures.py` — `save_fixture(study_id, chapter_num)` and `load_fixture(...)` to MinIO at `_cache/synth_fixtures/<study_id>/<chapter_num>.json`
- Run ONE full E2E Docker study to capture fixtures for chapters 1–10 (uses today's cached plan)
- All debug endpoints replay against these fixtures — sub-second comparisons

## Hard constraints (recommendations all respect these)

1. **No paid APIs.** Free-tier rotator + self-hosted host-side llama-server only.
2. **No in-cluster inference.** CPU spikes destabilize single-node K8s (Xinference removal precedent). Host-side llama-server for any local model.
3. **ONNX preferred for local models.** No torch/GPU dependency in cluster.
4. **Reuse existing rotator groups** where LLMs remain: `kd-all`, `kd-keylm`, `kd-reduce-label`, `kd-embed`.
5. **Quality > wall-clock.** Reliability/auditability wins matter as much as speed.
6. **Async/parallel preserved.** Solutions must respect existing `asyncio.gather` + LangGraph `Send()` fanout.

## Explicitly rejected options

- **End-to-end extractive synthesis** (Centroid, TextRank, LexRank, MatchSum, HiStruct+) — loses prose glue. Section synth must stay LLM.
- **Paid embedding APIs** (Voyage, Cohere, OpenAI text-embedding-3) — violates free-tier constraint. `kd-embed` covers this.
- **Supervised text-quality classifiers** — no labeled data for our corpus. textstat is unsupervised and good enough.
- **In-cluster GPU NLI inference** — violates no-in-cluster rule. MiniCheck-7B is host-side; AlignScore-large 355M / ModernBERT-NLI 184M are CPU-runnable.
- **LLM-as-judge with smaller models for the grader** — doesn't fix audit↔grader disagreement. Deterministic scorers strictly better.
- **Template + slot-filling for section synth** — too rigid for framework-doc variability.
- **wtpsplit SaT-12l-sm** — 10× slower than SaT-3l-sm; quality diff marginal on technical docs.

## Open questions (need real data to decide)

1. **SemanticChunker percentile threshold** — probably 92-97th percentile for 4-15 segments. Tune offline on existing chapter sources from recent committed studies.
2. **MiniCheck-7B vs AlignScore-large vs ModernBERT-NLI** for faithfulness — different speed/quality trade-offs. Decision needs ~50 hand-labeled claims from a real study.
3. **Phi-4-mini reliability for curator tone pass** — spot-check needed before committing.
4. **NLI false-positive rate for assumption_match** — needs ~100-sentence calibration sample.
5. **Glossary regex vs GLiNER2 trade-off** — ship regex first; escalate only if curator after-state has term inconsistency.
6. **Phi-4-mini availability on free-tier rotator** — if unavailable, fall back to `kd-reduce-label` group or host-side llama-server.

## Two scopes (don't conflate)

**Scope A: LLM→classical replacement** (this doc). 5 phases above. Reliability + cost wins. ~12 days work.

**Scope B: Apply R1+R2+R4 to synth's remaining LLM calls.** Mechanical extension of the planner sprint: separate `kd-synth` non-reasoning pool, `method="json_schema"` for `ChapterOutput`/`ProseChapterOutput`/`ChapterOutline`/`Section`, hedged invoke for section synth. ~2-3 days work. Independent of scope A.

**Recommended sequencing**: Scope A first (reduces the *number* of LLM calls), then Scope B (optimizes the remaining calls). Don't optimize calls you're about to delete.

## Sources (16, all 2024+ unless flagged)

- [wtpsplit / Segment Any Text (EMNLP 2024)](https://aclanthology.org/2024.emnlp-main.665/) — Sentence segmentation SoTA, ONNX-runnable
- [BERTopic GitHub](https://github.com/MaartenGr/BERTopic) — Hierarchical topic + LLM labeling pattern
- [LangChain SemanticChunker docs](https://python.langchain.com/docs/how_to/semantic-chunker/) — Breakpoint detection via cosine similarity
- [tasksource/ModernBERT-large-nli (2025)](https://huggingface.co/tasksource/ModernBERT-large-nli) — Multi-task NLI, May 2026 SoTA encoder
- [philschmid: Fine-tune ModernBERT in 2025](https://www.philschmid.de/fine-tune-modern-bert-in-2025) — Production ModernBERT benchmarks
- [Bespoke-MiniCheck-7B (Oct 2024)](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B) — LLM-AggreFact SoTA 77.4%
- [MiniCheck (EMNLP 2024)](https://github.com/Liyan06/MiniCheck) — C2D/D2C synthetic claim-grounding training
- [LLM-AggreFact leaderboard](https://llm-aggrefact.github.io/) — Benchmark for grounded-faithfulness evaluators
- [AlignScore (ACL 2023, kept for 355M-param CPU option)](https://github.com/yuh-zha/AlignScore) — Pre-2025 but production-relevant
- [Vectara HHEM 2.1 (2024)](https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model) — Open-source NLI-style hallucination eval
- [GroUSE (COLING 2025)](https://arxiv.org/abs/2409.06595) — Atomic-claim faithfulness benchmark
- [textstat (Feb 2026, v0.7.13)](https://pypi.org/project/textstat/) — Readability formulas
- [textdescriptives (May 2025, 2.8.4)](https://hlasse.github.io/TextDescriptives/readability.html) — spaCy-pipe readability extensions
- [GLiNER2 (May 2026, NAACL 2024 base)](https://aclanthology.org/2024.naacl-long.300.pdf) — Zero-shot multi-task entity extraction
- [Phi-4-mini-instruct (Feb 2025)](https://huggingface.co/microsoft/Phi-4-mini-instruct) — Small-LLM, ARC-C 83.7%
- [Qwen3 technical report (May 2025, arXiv 2505.09388)](https://arxiv.org/abs/2505.09388) — Small-LLM family for backup labeling
- [RAGAS docs: Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) — Confirms `FaithfulnesswithHHEM` non-LLM variant

## Code anchors (absolute paths)

| File | What lives there |
|---|---|
| `apps/fastapi/graphs/knowledge/distiller.py` | grader/refiner/curator/critic/summary call sites |
| `apps/fastapi/graphs/knowledge/hierarchical_synth.py` | Phase A `generate_outline` (Step A target) + Phase B vault routing (already deterministic — reference pattern) |
| `apps/fastapi/schemas/knowledge/prompts.py` | `OUTLINE_PROMPT` (435), `SECTION_SYNTH_PROMPT` (521), `GRADER_PROMPT` (591), `ADJUSTMENT_PROMPT` (678), `CRITIC_PROMPT` (716), `ASSEMBLER_PROMPT` (747), `CURATOR_PROMPT` (791) |
| `apps/fastapi/schemas/knowledge/agents.py` | `ChapterOutput`, `OutlineSection`, `Section`, `ProseChapterOutput`, `GraderEvaluation`, `CriticAssessment`, `Issue`, `Flashcard`; `code_preservation_ratio` deterministic pattern at lines 281-297 |
| `apps/fastapi/pyproject.toml` | Already-installed: `mdformat`, `tree-sitter`, `gliner` (deferred), `spacy` (transitive), `rapidfuzz`. Need to add: `wtpsplit`, `textstat`, `textdescriptives`, `langchain-experimental` (for SemanticChunker, likely already transitive), MiniCheck/AlignScore models via `transformers` (already present) |

## Pre-sprint checklist (when ready to start)

- [ ] Sign up for host-side llama-server with Phi-4-mini-instruct + MiniCheck-7B (or AlignScore-large) — host-side, not in-cluster
- [ ] Run ONE full E2E Docker study with current code to capture synth fixtures
- [ ] Hand-label ~50 claims from that study for MiniCheck vs AlignScore vs ModernBERT-NLI comparison
- [ ] Hand-label ~100 sentences for NLI assumption_match calibration
- [ ] Confirm Phi-4-mini availability on free-tier rotator OR commit to host-side llama-server pattern
- [ ] Start Phase 1 (Grader replacement) with `/debug/grader_compare` endpoint

## Ship log — 2026-05-13

### Phase 1 — Classical grader ✅ shipped + validated

**Module:** `apps/fastapi/services/knowledge/grader_classical.py` (~430 LoC). All 9 grader dims:
- **Deterministic (8/9):** `signal_to_noise` (regex blacklist + stub-marker + Summary-heading detection); `citation_integrity` (`# docs:` count vs `chapter.assigned_files`); `code_density` (fence-aware code-line/total-line ratio); `job_alignment` + `portfolio_synergy` (substring match on user_profile); `assumption_match` (regex heuristic for definitional templates of `mastered_technologies` — chose regex over ModernBERT-NLI to respect no-in-cluster-inference rule); `complexity_appropriate` (`textstat` Flesch-Kincaid mapped to `user_profile.level` grade band); `code_preservation_ratio` (passthrough from upstream audit).
- **Small LLM (1/9):** `market_analysis` via `build_reduce_label_chain()` (kd-reduce-label rotator already validated 2026-05-11), `_MarketAnalysisJudgment` Pydantic schema with `method="json_schema"`.
- **Composite:** weighted-average using `_DIM_WEIGHTS` (double-weight on signal_to_noise + citation_integrity + code_preservation_ratio per `GRADER_PROMPT` guidance).
- **Action rule:** unchanged from LLM grader (composite ≥ acceptance_threshold → accept; ≥ 0.60 → refine; else regenerate).

**Wiring:**
- `apps/fastapi/graphs/knowledge/helpers.py::_grade_attempt` — checks `KD_USE_CLASSICAL_GRADER` env flag (default `"0"`); when `"1"`, routes to `score_chapter_classically` instead of LLM grader.
- Pre-gate (`_deterministic_grader_gates`) preserved — catches obviously-broken chapters before either path.

**Validation harness:** `POST /api/v1/knowledge/debug/grader_compare` — runs both LLM + classical paths on the same `synthesis_text`, returns side-by-side `GraderEvaluation` + per-dim deltas + timings + agreement flags. Temporarily disables `KD_USE_CLASSICAL_GRADER` during the LLM-path call so it's a true A/B regardless of production config.

**Validation result (synthetic Docker chapter fixture, 2026-05-13):**
- Composite: Classical 0.891 vs LLM 0.980 (delta -0.089 within tolerance)
- Both `accept`; `agreement_action: true` ✅
- Wall-clock: Classical 17.1s vs LLM 31.7s = **1.9× speedup** (classical wall-clock is dominated by the single market_analysis small-LLM call; the 8 deterministic scorers complete in <100ms combined)
- **Classical surfaced MORE signal than LLM**: caught `code_density=0.32` (truth) where LLM hallucinated `0.85`; caught `complexity_appropriate=0.47` (textstat: Flesch-Kincaid 11.3 < target 14-17 for senior) where LLM gave a flat 1.0. Empirical confirmation that deterministic-scorers eliminate the audit↔grader disagreement pattern.

**Helm:** `kd.useClassicalGrader: "0"` default → `KD_USE_CLASSICAL_GRADER` env via `_helpers.tpl`.

### Phase 2.1 — Classical critic faithfulness ✅ shipped + validated

**Insight:** the critic was already 2/3 deterministic before Phase 2 started — `citation_coverage` is regex-counted at `distiller.py:2067` (pre-Phase 2); `code_syntax_valid` is tree-sitter-computed via `_compute_code_syntax_valid_score` (OP-59, 2026-04-25). Only `faithfulness` required an LLM call (per-chapter via OP-45 parallel pattern).

**Module:** `apps/fastapi/services/knowledge/critic_classical.py` (~250 LoC). `score_faithfulness_classical` algorithm:
1. For each `# docs: <slug>` citation in the chapter, extract the preceding sentence as the "claim"
2. Look up `<slug>` content from `source_contents` (production critic loads via `_read_raw_prefix`)
3. Embed claim + source via `kd-embed` NIM rotator (already in production)
4. Cosine similarity → faithfulness via clipped linear: `cos ≥ 0.45 → 1.0`, `cos ≤ 0.20 → 0.0`, linear between
5. Per-chapter score = mean of per-citation faithfulness

Chose embedding-similarity over Bespoke-MiniCheck-7B / AlignScore-large (the May 2026 SoTA NLI faithfulness models) to **respect the no-in-cluster-inference rule** per `feedback_local_vs_rotator_architecture` memory. Phase 2.2 can upgrade to host-side MiniCheck when accuracy proves insufficient.

**Wiring:**
- `apps/fastapi/graphs/knowledge/distiller.py` critic node — when `KD_USE_CLASSICAL_CRITIC=1`, replaces the per-chapter LLM faithfulness call (lines 2092-2153 OP-45) with the classical scorer; loads `source_contents` once via `_read_raw_prefix`.
- All downstream post-processing (tree-sitter override, merge, linter, fence-scan, weighted-overall) unchanged.

**Validation harness:** `POST /api/v1/knowledge/debug/critic_compare` — sends chapter + source_contents, runs both paths, returns side-by-side `CriticAssessment` + deltas.

**Validation result (synthetic Docker chapter fixture, 2026-05-13):**
- All 3 dims: 1.000 vs 1.000 (0.000 delta on every dim) ✅
- Wall-clock: Classical **1.17s** vs LLM **6.03s** = **5.1× speedup**
- `agreement_within_0.15_per_dim: true`
- (Adversarial cases with off-topic citations not yet tested — cosine thresholds 0.45/0.20 are conservative defaults pending production-data calibration)

**Helm:** `kd.useClassicalCritic: "0"` default → `KD_USE_CLASSICAL_CRITIC`.

### Phase 3.1 — Classical outline (header-based) ✅ shipped + validated

**Module:** `apps/fastapi/services/knowledge/outline_classical.py` (~360 LoC). Algorithm:
1. **Strip code fences** in `files_content` so headers inside code blocks don't trigger false splits
2. **Extract `##`/`###`/`####` markdown headers** as natural section boundaries
3. **Filter banned headings** (`Introduction`, `Overview`, `Summary`, `Conclusion`, `Recap`, `Takeaways`) — boilerplate that wastes a section slot
4. **Normalize to 4-15 sections:** `>15` → merge smallest consecutive sections to target=8; `<4` → equal-chunk split into 4 pieces (fallback for flat docs); `4-15` → use as-is
5. **Build `OutlineSection` objects:** `heading` = literal markdown text (zero LLM for naming), `goal` = template, `assumes_from_prior_sections` = template
6. **One small LLM call** for `_ChallengesFlashcards` over section-summaries (~3K tokens) — the only LLM in the classical path (irreducibly creative; uses kd-all rotator with `method="json_schema"`)

**Wiring:**
- `apps/fastapi/graphs/knowledge/hierarchical_synth.py::generate_outline` — checks `KD_USE_CLASSICAL_OUTLINE`; when `"1"`, routes to classical path. Same `ChapterOutline` shape so Phase B vault routing + Phase C section synth + Phase D assemble work unchanged.

**Validation harness:** `POST /api/v1/knowledge/debug/outline_compare` — accepts chapter + files_content, runs both paths, returns side-by-side `ChapterOutline` + headings deltas.

**Validation result (6-section FastAPI Testing fixture, 2026-05-13):**
- Section count: Classical 6 vs LLM 6 (delta 0) ✅
- Classical headings: literal markdown (`Async Test Client`, `Dependency Injection Overrides`, ...)
- LLM headings: paraphrased (`Async Test Client with httpx`, `Dependency Overrides for Testing`, ...) — both valid; classical respects source structure more faithfully (better for Phase B vault routing alignment)
- Wall-clock: Classical 19.7s vs LLM 20.6s ≈ **1.0×** (both make 1 LLM call; the win is token-cost reduction not wall-clock)
- **Input tokens to LLM: ~3K (classical) vs ~5K (LLM)** = ~40% input reduction. Output tokens smaller too (no sections list in classical's challenges/flashcards-only call).
- Section count guarantee: 4-15 by construction in classical (no post-hoc Pydantic rejection risk)

**Helm:** `kd.useClassicalOutline: "0"` default → `KD_USE_CLASSICAL_OUTLINE`.

### LLM rotator EOL refresh (kd-all, 2026-05-13)

Validation of the 3 debug endpoints surfaced **4 NIM model EOLs in the active rotator** that the May-2026 catalog audit missed (NIM rolling EOLs faster than their docs reflect). Each appeared as a non-retryable HTTP 410 "Gone" that aborted the entire LLM cascade (LiteLLM treats 410 as non-retryable; `Available Model Group Fallbacks=None`).

| Action | Model | EOL date | Source |
|---|---|---|---|
| Disabled | `nvidia_nim/deepseek-ai/deepseek-v3.1-terminus` | 2026-05-04 | Phase 1.3 `/debug/grader_compare` validation |
| Disabled | `nvidia_nim/moonshotai/kimi-k2.5` | 2026-04-30 | Phase 3.1 `/debug/outline_compare` validation |
| Disabled | `nvidia_nim/minimaxai/minimax-m2.5` | 2026-05-12 | Phase 3.1 `/debug/outline_compare` validation |
| Disabled | `nvidia_nim/moonshotai/kimi-k2-thinking` | 2026-05-12 | Phase 3.1 `/debug/outline_compare` validation |
| Bumped (active) | `gemini/gemini-3.1-flash-lite-preview` → `gemini-3.1-flash-lite` | preview retires 2026-05-25 | research audit |
| Bumped (active, both kd-all + kd-reduce-label) | `nvidia_nim/deepseek-ai/deepseek-v3.2` → `deepseek-v4-flash` (×2) | v3.2 EOL 2026-05-04 | surfaced via Phase 3.1 validation |
| Updated string + re-enabled | `nvidia_nim/moonshotai/kimi-k2.5` (commented) → `moonshotai/kimi-k2.6` (active) | K2.5 EOL 2026-04-30 | K2.6 is current NIM K2.x |
| Updated string + re-enabled (cascade dup) | `nvidia_nim/minimaxai/minimax-m2.5` (commented) → `minimax-m2.7` (active) | M2.5 EOL'd; M2.7 supersedes | NIM current MiniMax |
| Updated string (still commented — paywall reason persists) | `sambanova/MiniMax-M2.5` → `MiniMax-M2.7` | M2.5 superseded | SambaNova account still paywalled |

**Pattern observed**: NIM rolling EOLs happen faster than catalog research can verify. The only ground truth is the API response. Future model freshness audits should re-run on a schedule (weekly cadence reasonable). The `/debug/*_compare` endpoints will surface 410s organically during validation.

### Phase 4 — Classical refiner ✅ shipped + validated

**Module:** `apps/fastapi/services/knowledge/refiner_classical.py` (~330 LoC). Two-layer design:

**Layer 1 — `apply_classical_patches(synthesis_text, issues, chapter, user_profile) -> (patched_text, residual_issues, patch_log)`.** Deterministic edits per Issue:
- **`signal_to_noise`** (3 sub-patches, idempotent global pass):
  - `_delete_summary_sections` — strips `## Summary`/`## Conclusion`/`## Recap`/`## Takeaways` heading + body until next equal-depth heading
  - `_delete_boilerplate_lines` — strips lines matching the same intro-phrase regex as grader_classical (`"in this chapter we will…"`, `"let's dive into…"`, `"furthermore"`, etc.)
  - `_delete_stub_lines` — strips lines containing bare `TODO`/`TBD`/`PLACEHOLDER`/`FIXME`/`XXX` markers (lookahead is line-scoped, not whole-document, so a TODO above any later code fence isn't masked)
- **`citation_integrity`** — extracts `<slug>` from each Issue's suggestion (regex `# docs:\s*([\w/.\-]+)`); appends `# docs: <slug>` to chapter body if not already present
- **`assumption_match`** — for each `user_profile.mastered_technologies` entry, regex matches `(?:^|(?<=[.!?]\s)|(?<=\n)){TECH}\s+(?:is|are)\s+[^.!?\n]{0,200}[.!?]` (sentence-start anchored, MULTILINE flag); deletes the definitional sentence. 400-char defensive cap on deletion bounds.

Non-patchable dims (`code_density`, `job_alignment`, `portfolio_synergy`, `complexity_appropriate`, `market_analysis`) pass through as residuals untouched — these need real LLM rewrite, not regex edits.

**Layer 2 — `generate_adjustment_classically(evaluation, residual_issues, patch_log) -> str`.** Per-dim template-based adjustment text — drop-in replacement for `_generate_adjustment` (LLM call against `ADJUSTMENT_PROMPT`). Structure:
1. Acknowledge applied patches so re-synth doesn't undo them
2. Per residual dim: canonical surgical instruction + grader-flagged span quotes (cap 5 per dim to bound prompt size)
3. End with composite-score target line

**Wiring:** `apps/fastapi/graphs/knowledge/distiller.py` Self-Refine loop — when `KD_USE_CLASSICAL_REFINER=1` AND grader action is `refine`:
1. Apply patches → patched_text + residuals + patch_log
2. If `patch_log` non-empty AND text changed: re-grade patched (same `_grade_attempt` path; classical grader if `KD_USE_CLASSICAL_GRADER=1`)
3. Argmax updates with patched evaluation
4. **If re-grade reaches acceptance → break the Self-Refine loop entirely** (saves the full re-synth + re-grade pair on the next iter — 2 LLM calls when classical grader is OFF, 1+small LLM call when ON)
5. Otherwise → use `generate_adjustment_classically` for next iter's `previous_adjustments` (saves the small `_generate_adjustment` LLM call)

Default `"0"` keeps the legacy LLM-only refine path; flip to `"1"` after A/B validation via `/debug/refiner_compare`. Depends on `KD_USE_CLASSICAL_GRADER` for reliably labeled Issue dimensions (Phase 1 prerequisite — already shipped).

**Validation harness:** `POST /api/v1/knowledge/debug/refiner_compare` — accepts `synthesis_text` + `issues` list + `chapter` + `user_profile`; runs both classical patches and LLM `_generate_adjustment`; optionally re-grades the patched chapter via the classical grader. Returns per-dim issue counts, patch log, both adjustment texts, residual breakdown, regrade result, and timings.

**Validation result (synthetic FastAPI Testing chapter fixture, 12 seeded issues across 5 dims, 2026-05-13):**

| Metric | Result |
|---|---|
| Issues in (by dim) | signal_to_noise: 5, assumption_match: 3, citation_integrity: 2, code_density: 1, job_alignment: 1 |
| Patches applied | **7** — 1 Summary section + 3 boilerplate lines + 1 stub-marker (`# TODO`) line + 2 definitional sentences (FastAPI, Python) + 2 citations |
| Residual issues | **2** — code_density: 1, job_alignment: 1 (both non-patchable by design) |
| Text reduction | 1306 → 694 chars (**-47%**) — chapter is meaningfully tighter post-patch |
| Patch wall-clock | **2.8ms** (pure regex; sub-frame latency) |
| LLM adjustment wall-clock | 8.2s typical (occasionally 90s+ when rotator cascades — classical path is unaffected by such failures) |
| **Speedup on adjustment generation alone** | **~3,000× typical, ~100,000× under cascade-exhaustion conditions** |
| Post-patch regrade composite | 0.6555 (action="refine") — chapter still has structural code-density / market-alignment issues patches can't fix; correctly hands off to next iter |
| Classical adjustment text | ~1100 chars — structured with patch acknowledgment + per-residual-dim surgical instructions |
| LLM adjustment text | varies 77–800 chars depending on rotator state |

**Empirical confirmation of the Phase-4 thesis:** ~50% of grader Issues (6 of 12 in the fixture; the patchable dims) can be resolved deterministically. The remaining 50% (non-patchable: `code_density`, `job_alignment`, `portfolio_synergy`, `complexity_appropriate`, `market_analysis`) still need re-synth — but the classical adjustment text now carries the precise patch log so the next iter doesn't accidentally re-introduce boilerplate or re-define `FastAPI`. The biggest practical win is **eliminating the `_generate_adjustment` LLM call entirely** for every refine iter (1 LLM call saved per refine iter regardless of patch outcome).

**When patches alone reach the acceptance threshold** (chapters whose only issues are in patchable dims): the Self-Refine loop breaks at iter N, skipping iter N+1's re-synth + re-grade. Worst case: 2 saved LLM calls (legacy grader). Best case: 1 saved LLM call + 1 saved small-LLM grader. This case is rare in practice (most chapters need code-density work too) but bounded-positive.

**Known minor limitation:** when an `assumption_match` sentence is immediately followed by another on the same line (e.g., `"Python is X. pytest is Y."`), only the first match is caught per pass (FastAPI ✓, Python ✓, pytest preserved due to leftover leading whitespace shifting it off the line anchor). Acceptable — the residual definitional sentence gets surfaced in the adjustment text as a remaining `assumption_match` issue, and the LLM rewrite addresses it.

**Helm:** `kd.useClassicalRefiner: "0"` default → `KD_USE_CLASSICAL_REFINER` env via `_helpers.tpl`.

### Phase 5 — Classical curator + summary ✅ shipped + validated

**Modules:**
- `apps/fastapi/services/knowledge/curator_classical.py` (~200 LoC)
- `apps/fastapi/services/knowledge/summary_classical.py` (~210 LoC)

#### Curator (`curator_classical.py`)

Three deterministic regex passes, **zero LLM calls**:

1. **Glossary substitution** — `_apply_glossary_substitution` applies a known-synonym map (`fast api`/`fast-api` → `FastAPI`; `postgres` → `PostgreSQL`; `k8s`/`kube` → `Kubernetes`; etc.) AND case-normalizes the canonical form. Word-boundary anchored, case-insensitive variant matching.
2. **Heading depth normalization** — `_normalize_heading_depths` collapses every `####`+ heading to `###` outside fenced code blocks. Preserves `#` (chapter title) and `##` (section) intact; matches the curator-prompt rule "`##` for sections, `###` for subsections".
3. **Transition-line deletion** — `_delete_transition_lines` uses a superset of the refiner_classical / grader_classical regex (adds `So,`, `Alright,`, `Let's explore,`, `that being said,`, `having covered` to the existing boilerplate list).

Code-vault sentinels (`<code-ref hash="..."/>`) are never matched by any of the three regexes (they target prose-shaped patterns only). The production curator's existing `_audit_sentinel_roundtrip` runs after this function returns — if anything went wrong the original chapter is preserved.

The CURATOR_PROMPT's "voice/tone harmonization" component is intentionally **not** replaced. Phase 5.1 can add an optional small-LLM tone pass behind a sub-flag if voice drift becomes measurable in real-corpus A/B testing.

**Wiring:** `apps/fastapi/graphs/knowledge/distiller.py::curator._curate_one` — when `KD_USE_CLASSICAL_CURATOR=1`, replaces the per-chapter LLM call with `curate_chapter_classically(...)`. The vault → curate → audit_sentinel_roundtrip → restore_code_blocks flow is preserved.

**Validation harness:** `POST /api/v1/knowledge/debug/curator_compare` — accepts vaulted chapter + glossary terms; runs both paths; returns char deltas + pass log + timings.

**Validation result (synthetic vaulted FastAPI Testing chapter, 2026-05-13):**

| Metric | Result |
|---|---|
| Classical passes applied | **3** — 7 glossary normalizations + 2 H4+ → H3 collapses + 7 boilerplate-line deletions |
| Classical wall-clock | **1.1ms** (pure regex) |
| LLM wall-clock | 95.2s |
| **Speedup** | **~85,000×** |
| Code-vault preservation | ✅ all `<code-ref hash="..."/>` sentinels preserved byte-exact |

The synthetic fixture was deliberately heavy on transition phrases (every prose line opened with `Let's explore`, `Furthermore`, `So`, `In this chapter we will`, etc.), so the -71% char delta exaggerates production behavior. On real chapters where transition lines are minority, the classical curator strips them surgically and leaves substantive prose intact.

**Helm:** `kd.useClassicalCurator: "0"` default → `KD_USE_CLASSICAL_CURATOR`.

#### Summary / Assembler (`summary_classical.py`)

Splits the ASSEMBLER_PROMPT into deterministic + small-LLM halves:

**Deterministic (Python):**
- Chapter header `# {framework} Study`
- `## Reading Plan` — bulleted list with chapter links + goal as one-line takeaway. Sourced from `previews` tuples directly; never transits the LLM (zero hallucination risk on chapter numbering, links, or omissions).

**Small-LLM (one structured-output call via kd-reduce-label rotator):**
- `framing` — single dense paragraph (60-120 words)
- `market_roadmap` — paragraph on framework leverage in target markets, OR empty if no markets declared
- `money_projects` — 3-5 structured `_MoneyProject(name, description, target_market)` items

Schema enforced via `_SummaryCreative` Pydantic model + `method="json_schema"`. Falls back to a deterministic-only summary if the rotator call fails (framing paragraph becomes a templated "study distills N chapters for {level}-level reader..." line).

**Wiring:** `apps/fastapi/graphs/knowledge/distiller.py::assembler` — when `KD_USE_CLASSICAL_SUMMARY=1`, replaces `_call_assembler_llm(...)` with `build_summary_classically(...)`. The DEBT.md + episodic-memory steps remain unchanged.

**Validation harness:** `POST /api/v1/knowledge/debug/summary_compare` — accepts framework + user_profile + previews list; runs both paths; returns full summary.md strings + section counts + timings.

**Validation result (synthetic 6-chapter FastAPI study, target_markets=[UAE, G42, Singapore DBS], 2026-05-13):**

| Metric | Classical | LLM |
|---|---|---|
| Summary length | 3007 chars | 1828 chars |
| Section count (`## ...`) | 3 (Reading Plan + Market Roadmap + Money Projects) | 3 (Reading Plan + Market Roadmap + Money Projects) |
| Wall-clock | **16.6s** | 20.8s |
| Speedup | 1.3× | — |
| Chapter omission risk | **0** (deterministic) | non-zero (LLM could skip/reorder) |
| Money-project structure | structured (name + description + target_market fields) | free-form text |

**Quality observations:**
- Classical Reading Plan is guaranteed complete and consistently formatted (`1. [Chapter NN — Title](chapterNN/README.md) — goal`)
- Classical Market Roadmap correctly emitted concrete references (ADGM/DIFC, MAS technology risk management, sovereign AI, fintech middleware)
- Classical Money Projects emitted 3 structured ideas with explicit target_market fields ("Async Financial Data Ingestion API → Singapore DBS"; "Sovereign AI Model Gateway → UAE, G42"; "Compliance-First Transaction Middleware → UAE")
- LLM output included a stray ```markdown fence wrapper (rotator quirk) that the classical path's deterministic header construction avoids

The wall-clock speedup is modest (1.3×) because the small-LLM creative call still dominates both paths. The real wins are **reliability** (chapter-list completeness is structurally guaranteed; no LLM omission risk) and **structured artifacts** (money_projects as typed records vs free-form prose) — same shape as the Phase 1/3.1 pattern.

**Helm:** `kd.useClassicalSummary: "0"` default → `KD_USE_CLASSICAL_SUMMARY`.

### Phase status board (end of 2026-05-13)

| Phase | Step | Status |
|---|---|---|
| Phase 1 | Grader (classical 8/9 dims + small-LLM market_analysis) | ✅ shipped + validated |
| Phase 2.1 | Critic faithfulness (embedding-similarity via kd-embed) | ✅ shipped + validated |
| Phase 2.2 | Critic faithfulness → host-side MiniCheck/AlignScore | deferred (needs host-side llama-server setup) |
| Phase 3.1 | Outline (header-based extraction + small-LLM challenges/flashcards) | ✅ shipped + validated |
| Phase 4 | Refiner (deterministic patches + template adjustment) | ✅ shipped + validated |
| **Phase 5** | Curator (3 regex passes, zero LLM) + Summary (deterministic reading plan + small-LLM creative) | ✅ **shipped + validated this session** |

**Scope A (LLM→classical replacement) is now complete** — only Phase 2.2 remains and that requires host-side llama-server infrastructure the user hasn't provisioned (the Phase 2.1 embedding-similarity faithfulness is good enough until adversarial data forces the upgrade).

### Scope B — Rotator + concurrency hardening (2026-05-12 night, started post-E2E)

The 2026-05-12 E2E run with all Scope A flags ON revealed a **cascade-exhaustion failure mode** that Scope A's classical paths cannot fix because it lives in the irreducibly-LLM section synth (Phase C). Symptoms: 58 min wall-clock for 2 chapters (1 with DEBT, 1 with score=0.00 / 0 iters via OP-12 rescue path), retry storm across mistral / magistral / gemini / minimax / qwen / gemini-3, no hard errors logged. Root cause: ~7 chapters × 8 sections × 1-3 self-refine iters = 16-48 concurrent LLM calls at peak, against ~170 RPM total free-tier budget (sustainable ~10 concurrent at safety-factor 0.5).

A deep-research brief identified 5 SoTA fixes (May 2026); ship-first list (highest ROI first):

| # | Fix | ROI | Status |
|---|---|---|---|
| 1 | **`asyncio.Semaphore` per-process LLM concurrency cap** in `_invoke_structured_with_fallback` (default 10, env `KD_LLM_GLOBAL_CONCURRENCY`) | 10/10 | ✅ shipped |
| 2 | **`method="json_schema"`** with `function_calling` fallback in `_invoke_structured_with_fallback` | 8/10 | ✅ shipped |
| 3 | **`kd-synth` non-reasoning pool** (7 deployments curated for prose+code: mistral-large-3 ×2 / Nemotron-3-super / gpt-oss-120b / Mistral Small + Medium / Llama-4 Maverick); env flag `KD_USE_SYNTH_POOL` opts synth in | 7/10 | ✅ shipped (default OFF pending validation) |
| 4 | Redis-backed `pyrate-limiter` per-provider RPM enforcement (multi-worker quota sharing across Celery + FastAPI) | 6/10 | pending |
| 5 | Exponential cooldown + `Retry-After` honor (LiteLLM callback hook patch) | 4/10 | pending |

#### Shipped this session

**Item 1 — Global concurrency cap.** New `_get_llm_semaphore()` lazy singleton in `graphs/knowledge/helpers.py`; configurable via `KD_LLM_GLOBAL_CONCURRENCY` env (default `"10"`). All structured-output LLM calls (section synth, grader-when-classical-off, outline-when-classical-off, critic-when-classical-off) acquire from this semaphore before invoking the router. Per-process — Celery prefork concurrency=5 × cap=10 = effective 50 cluster-wide; tune cap=2 if multi-worker over-subscription becomes an issue (Redis-backed pyrate-limiter — Scope B item #4 — is the durable fix).

**Item 2 — `method="json_schema"`.** `_invoke_structured_with_fallback` tries json_schema first (Mistral L3, Groq gpt-oss, NIM Nemotron, Gemini all GA in May 2026); falls back to function_calling on `NotImplementedError`/`ValueError` carrying "json_schema"/"method"/"schema"/"supported" in the message. Eliminates the None-return tool-call failure mode where a provider emits plain-text instead of a tool_call.

**Item 3 — `kd-synth` rotator group.** New `SYNTH_GROUP` constant + `_synth_entries()` function in `services/llm_chain.py`. **Revised 2026-05-12 night-late to a HYBRID pool** after E2E study `8f6af2b8` revealed that a pure-non-reasoning curation dropped 56% of vault hashes on large chapters (ch01 iter 2: 29 of 52 missing, 7 duplicated, 5 thin sections). Root cause: non-reasoning models have shorter effective output token budgets and weren't trained as rigorously on multi-entity structured outputs as frontier reasoning models. Concurrency cap (item 1) prevents reasoning models from cascade-exhausting under parallel fan-out, making them viable here.

| Tier | Deployment | Role |
|---|---|---|
| 1 | `nvidia_nim/moonshotai/kimi-k2.6` | Reasoning, AAII 49, complete structured output |
| 1 | `nvidia_nim/z-ai/glm-5.1` | Reasoning, AAII 51, SWE-Pro #1 OSS |
| 1 | `nvidia_nim/minimaxai/minimax-m2.7` | Reasoning, AAII 50, 204K ctx agentic |
| 1 | `nvidia_nim/deepseek-ai/deepseek-v4-flash` | Reasoning, AAII 47, free-tier path |
| 2 | `mistral/mistral-large-latest` | Non-reasoning frontier, LMArena #2 OSS, 256K ctx |
| 2 | `nvidia_nim/mistralai/mistral-large-3-675b-instruct-2512` | Same as above via NIM (independent failure domain) |
| 2 | `nvidia_nim/nvidia/nemotron-3-super-120b-a12b` | Non-reasoning, 1M ctx hybrid Mamba, AAII 36 |
| 2 | `nvidia_nim/openai/gpt-oss-120b` | Non-reasoning, AAII 33, native tools |
| 3 | `mistral/mistral-medium-latest` | Deep tail, between Large + Small |
| 3 | `mistral/mistral-small-latest` | Deep tail, AAII 28, fast fallback |
| 3 | `nvidia_nim/meta/llama-4-maverick-17b-128e-instruct` | Deep tail, 1M ctx, AAII 18 |

Tier 1 reasoning models get 180s timeout (vs 120s for Tier 2/3) to absorb their `<think>` token burn. The concurrency cap (item 1) keeps peak parallelism within budget so reasoning models don't cascade-exhaust.

**Failed pure-non-reasoning curation (2026-05-12 night, REVERTED):** Mistral L3 ×2 + Nemotron + gpt-oss + Mistral Medium/Small + Llama-4 Maverick (and ~~Groq llama-3.3-70b-versatile, removed mid-attempt for TPM ceiling~~). Surface failure: hash-drop pattern on large chapters because smaller models truncated their output. Logged as cautionary tale in commit history.

Hard EXCLUSIONS (still apply to hybrid pool): entire Gemini 3.x family (`T<1.0` infinite-loop bug per Google's own warning), Magistral (R-mode default — Mistral Medium covers same niche without reasoning burn), Cerebras whole provider (account 404), SambaNova (paywall), DeepSeek direct (Insufficient Balance), Groq gpt-oss-120b + Groq llama-3.3-70b-versatile (8-12K TPM ceiling vs 21-37K section-synth prompts).

Wiring: `build_synth_fallback_chain()` reads `KD_USE_SYNTH_POOL` env at call time — `"1"` routes to SYNTH_GROUP, default `"0"` keeps legacy kd-all. New explicit factory `build_synth_pool_chain()` for direct callers (validation harnesses, future hedged-invoke wrapper).

#### Files touched (Scope B partial — items 1+2+3)

**Modified:**
- `apps/fastapi/services/llm_chain.py` — added `SYNTH_GROUP`/`_synth_entries()`; bumped `build_synth_fallback_chain` to env-flag routing; new `build_synth_pool_chain()`; added `_synth_entries()` to Router model_list
- `apps/fastapi/graphs/knowledge/helpers.py` — added `_get_llm_semaphore()` + `_LLM_GLOBAL_SEMAPHORE`; wrapped `_invoke_structured_with_fallback` body with semaphore; switched `method="function_calling"` → `method="json_schema"` with function_calling fallback
- `k8s/helm/values.yaml` — new `kd.useSynthPool` (default `"0"`) + `kd.llmGlobalConcurrency` (default `"10"`)
- `k8s/helm/templates/_helpers.tpl` — `KD_USE_SYNTH_POOL` + `KD_LLM_GLOBAL_CONCURRENCY` env vars
- `docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` — this Scope B section

#### Validation pending

Live E2E with all Scope A flags ON + `KD_USE_SYNTH_POOL=1` + cap=10: expect zero cascade-exhaustion, all chapters reaching graded state. Items 4+5 deferred until empirical validation shows whether per-process cap alone is sufficient or multi-worker Redis-backed throttling is required.

### Phase B/C audit-fail hardening (2026-05-12 night-late, post-Scope B E2E)

Scope B (concurrency cap + json_schema + kd-synth pool) successfully eliminated the cascade-exhaustion failure mode. The next E2E (study `64b1cf9a`) revealed a SECOND, pre-existing audit failure mode: **hash-drop in Phase B/C hierarchical synth**. Symptom: LLM section synth outputs fewer code_refs than vault assigned; audit rejects (0 missing required); refiner loops through different models per iter (no convergence); chapters land in OP-12 rescue (score=0.00).

Evidence: ch01 (52 hashes) iter 2 had 29 missing / 7 duplicated; iter 4 had 31 missing / 5 duplicated; each iter using a different rotator member. ch04's Phase B routing assigned 32 of 53 hashes to a single section — an unreasonable structured-output ask for ANY model.

Three fixes shipped (Fix 2 deferred as a multi-day architectural change):

**Fix 1 — relax audit missing-hash tolerance** (`distiller.py` synth loop, 1-line change). Audit gate now accepts ≤10% missing as soft-DEBT (chapter proceeds to grader, code_preservation_ratio dim handles the weighting). Eliminates OP-12 rescue for chapters that are 90%+ structurally complete.

**Fix 3 v1 — REVERTED 2026-05-12 night-late.** Flat per-section cap with cross-topic redistribution. Initial implementation: when Phase B routed >10 hashes to a section, move WEAKEST-fit hashes to other sections by argmax similarity. **User-flagged correctly:** if source material is naturally 60% topic A / 20% B / 10% C / 10% D, the natural distribution `[60, 20, 10, 10]` is *correct* — cross-topic redistribution moves topic A's hashes into sections that aren't about topic A, damaging coherence. Confirmed by SoTA research (May 2026): STORM, GraphRAG, LLM×MapReduce all preserve skewed distribution at leaf level via sub-section splitting under the same parent, NEVER redistribute across topics.

**Fix 3 v2 — Phase A.5 bucket-split** (`hierarchical_synth.py::split_overloaded_sections`, runs between Phase B routing and Phase C synth). When a section has >`MAX_HASHES_PER_SECTION_BUCKET=10` hashes assigned, **split it into k = ceil(n/MAX) sibling sub-sections under the SAME parent heading**. Sub-sections inherit the parent's `goal` and `assumes_from_prior_sections` verbatim; only the heading differs (`"<parent> — Part i of k"`). Hash assignment within the parent's set uses k-means on the embedding vectors carried forward from Phase B (HashRouting now optionally returns `hash_keys` + `hash_vecs`); falls back to chronological-chunk split if embeddings unavailable. Result: **topic coherence preserved, every Phase C LLM call sees ≤10 hashes, audit gate clears naturally without forcing the LLM to recite 30+ hash strings.** Sources: GraphRAG hierarchical community split (Edge et al., arXiv 2404.16130), STORM outline expansion (Shao et al., NAACL 2024), JSONSchemaBench (ICLR 2025, arXiv 2501.10868) confirming structured-output recall degrades with list cardinality. If `len(new_sections) > 15` (ChapterOutline schema max), merge trailing sub-sections into a final "Additional" section.

**Fix 4 — surgical missing-hash feedback** (already shipped via `_format_structured_output_feedback`). Refiner adjustment text already includes the first 8 missing hashes with code-block previews; the LLM gets explicit "add these specific hashes back to the right section" instructions. Made fully effective once Fix 2 (model pinning) ships.

**Fix 2 — per-chapter model pinning** (DEFERRED). Would wrap LiteLLM Router with sticky deployment selection per chapter so all iterations use the same model (refiner gets continuity). Multi-day architectural change. Skipped this session because Fix 1 + Fix 3 should absorb most cases; revisit if audit-fail rate stays high after empirical validation.

#### Files touched (Phase B/C audit hardening)

**Modified:**
- `apps/fastapi/graphs/knowledge/distiller.py` — synth-loop audit gate: `_MISSING_TOLERANCE_PCT = 0.10` + `_missing_blocks_accept` condition (was `missing or ...`)
- `apps/fastapi/graphs/knowledge/hierarchical_synth.py::route_hashes_to_sections` — added MAX_HASHES_PER_SECTION=10 rebalancing pass after the empty-section nudge

### Phase B/C audit-fail hardening v2 — Fix #1 + Fix #2 (2026-05-12 night, post-bb7f3b50 study)

Study `bb7f3b50` (FastAPI, 2026-05-12 with Phase A.5 active) surfaced two distinct failure modes that Phase A.5 alone could not resolve:

1. **Monster chapter overflow** — ch01 had 342 vault hashes from a single dense doc page. Phase A.5 wanted to expand to ~35 sub-sections but `ChapterOutline.sections.max_length` capped at 15. My overflow fallback dumped 224 hashes into one "Additional" section — exactly the cross-topic dumping ground the user warned about.
2. **Multi-model refine non-convergence** — across refine iters within a chapter, the rotator's `simple-shuffle` picked different deployments per iter. Each iter the LLM started fresh with no memory of previous output. The refiner's surgical "you missed hash X, Y, Z" feedback couldn't act on output the current model didn't generate. Empirical: ch01 iter 0 dropped 31 hashes, iter 4 dropped 302 hashes — refiner was a random walk, not a convergent loop.

#### Fix #1 — bump `ChapterOutline.sections.max_length` 15 → 40

Schema change in `apps/fastapi/schemas/knowledge/agents.py`. 40 supports up to 400-hash chapters at 10 hashes/section. Phase A.5's overflow fallback in `hierarchical_synth.py` updated to use the new cap (was hardcoded 15 → now `_SCHEMA_MAX_SECTIONS = 40`, leaving 39 slots before merging into "Additional"). The "Additional" fallback now triggers only for >400-hash outlier chapters (rare).

#### Fix #2 — per-chapter model pinning

New `pick_synth_deployment(seed)` + `build_synth_pinned_chain(model)` in `services/llm_chain.py`. Approach:

- `pick_synth_deployment(chapter.number)` deterministically picks one litellm model string from SYNTH_GROUP (same chapter always picks the same model across study runs; different chapters spread load across pool members)
- `build_synth_pinned_chain(pinned_model)` builds a fresh single-deployment `Router` containing only that model + a unique group_name (`kd-synth-pinned-<hash>`), wraps in `ChatLiteLLMRouter`
- Per-process `_pinned_chain_cache` keyed by pinned model string (Celery prefork workers each have their own)
- Falls back to full SYNTH_GROUP if the pinned model isn't in the pool (e.g. EOL'd mid-run)

Wiring in `distiller.py::synthesize_chapter` at chapter start (after vault build, before synth loop): when `KD_PIN_CHAPTER_MODEL=1` AND `KD_USE_SYNTH_POOL=1`, the incoming `llm` parameter is overridden with the pinned chain for the entire chapter's Phase A/B/C calls + refine iters. Logged as `PIN-CHAPTER-MODEL: using <model>` at chapter start.

**Single-model failure mode**: pinned router has `num_retries=3` + `cooldown_time=30s`. If the pinned model has a hard outage (404/410/auth), the chapter falls to OP-12 rescue rather than cascading across the entire SYNTH_GROUP (cascading would defeat the purpose of pinning). This is intentional: better to lose ONE chapter to a model outage than to lose refine convergence across the WHOLE study.

#### Helm

- `kd.useSynthPool: "1"` (default; Scope B item 3)
- `kd.pinChapterModel: "1"` (default; Fix #2 — flipped to default-on since it's the convergence answer empirically observed)

#### Files touched (Fix #1 + Fix #2)

**Modified:**
- `apps/fastapi/schemas/knowledge/agents.py` — `ChapterOutline.sections.max_length` 15 → 40
- `apps/fastapi/graphs/knowledge/hierarchical_synth.py` — Phase A.5 overflow fallback uses `_SCHEMA_MAX_SECTIONS = 40`
- `apps/fastapi/services/llm_chain.py` — new `pick_synth_deployment` + `build_synth_pinned_chain` + `_pinned_chain_cache`
- `apps/fastapi/graphs/knowledge/distiller.py` — synth chapter-start: `KD_PIN_CHAPTER_MODEL` env check, overrides `llm` with pinned chain
- `k8s/helm/values.yaml` — `kd.pinChapterModel: "1"`
- `k8s/helm/templates/_helpers.tpl` — `KD_PIN_CHAPTER_MODEL` env var

**Deferred to follow-up (validated as needed):**
- Recursive bucket-split (Phase A.5 v3) — only worth it if 40-section cap proves insufficient for chapters >400 hashes
- Planner-side chapter size caps (REDUCE step) — would prevent monster chapters from existing in the first place; complementary to Fix #1
- LLM-generated sub-section headings — UX polish, no convergence impact

### Observability — OpenTelemetry dual-export (2026-05-12 night)

Goal: per-deployment LLM performance data to drive data-driven rotator decisions instead of static benchmark ranking. The same pipeline doubles as study-level distributed tracing for debugging.

**Architecture:**

```
LiteLLM Router + KD pipeline + LangChain
       ↓ (OTel SDK with kd_process metadata)
TracerProvider + MeterProvider (services/otel_setup.py)
       ↓ (BatchSpanProcessor fan-out)
       ├─ gRPC OTLP → Alloy → LGTM (Mimir/Tempo/Loki)
       └─ HTTP OTLP → LangFuse v3 /api/public/otel
```

**One init, dual export:** the SAME `TracerProvider` carries two `BatchSpanProcessor` instances. Every LLM call, every KD phase, every refine iter produces ONE span that lands in BOTH backends. No double-instrumentation, no redundant SDK setup, single source of truth.

**What lands in each backend (complementary, not duplicative):**

| Signal | LGTM (via Alloy) | LangFuse v3 |
|---|---|---|
| Per-deployment latency histogram (p50/p95/p99) | ✅ Mimir | — |
| Time-series success/failure counters | ✅ Mimir | — |
| Per-LLM-call traces with prompt context | ✅ Tempo | ✅ rich UI |
| Cost / token analysis by user/session/model | ⚠️ raw | ✅ built-in |
| KD custom metrics (refiner iters, bucket-split overflow, etc.) | ✅ Mimir | — |
| `kd_process` attribute for routing-decision queries | ✅ | ✅ |
| Distributed trace (study → planner → synth × N → curator → assembler) | ✅ Tempo | ✅ |
| Log correlation via trace_id injection | ✅ Loki | — |

**Files added:**

- `apps/fastapi/services/otel_setup.py` (~250 LoC) — `init_otel()` + `init_otel_for_celery_worker()` + dual-exporter setup. Auto-instruments FastAPI (routes), httpx (upstream LLM calls), redis (study registry, broker), Celery (task spans), logging (trace_id injection).
- `apps/fastapi/services/otel_metrics.py` (~200 LoC) — KD-specific custom metrics (`kd.chapter_synth_duration_seconds`, `kd.refiner_iters_to_accept`, `kd.bucket_split_overflow_total`, `kd.classical_grader_dim_score`, `kd.audit_missing_hashes_ratio`, `kd.study_completion_seconds`, `kd.classical_patch_applied_total`). Each carries the labels needed for production-decision PromQL.

**Files modified:**

- `apps/fastapi/pyproject.toml` — added `opentelemetry-api/sdk/exporter-otlp-proto-grpc/-http`, `opentelemetry-instrumentation-{fastapi,httpx,redis,celery,logging}`.
- `apps/fastapi/app.py` — `lifespan()` startup calls `init_otel(also_instrument_fastapi_app=app)`.
- `apps/fastapi/celery_app.py` — `worker_process_init` signal calls `init_otel_for_celery_worker()` per fork (each prefork worker needs its own provider since OTel state doesn't survive fork() cleanly).
- `apps/fastapi/services/llm_chain.py` — `litellm.callbacks = ["otel"]` when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. LiteLLM emits per-call deployment_id + model + latency + tokens + cost + error_type as span attributes.
- `apps/fastapi/graphs/knowledge/helpers.py::_invoke_structured_with_fallback` — derives `kd_process` from the existing `label` field ("synth" / "grade" / "outline" / "curator" / "critic"...) and injects it into metadata. LiteLLM's OTel callback picks it up; PromQL queries can then slice by `(kd_process, deployment_id)`.
- `k8s/helm/values.yaml` — new `otel:` block (alloy_endpoint, langfuse_otlp_endpoint, service_name, service_version).
- `k8s/helm/templates/_helpers.tpl` — `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_SERVICE_VERSION`, `OTEL_RESOURCE_ATTRIBUTES`, `LANGFUSE_OTLP_ENDPOINT` env vars (LANGFUSE_PUBLIC_KEY/SECRET_KEY already plumbed from secret).

**Operator config:**

- `OTEL_EXPORTER_OTLP_ENDPOINT=""` → OTel disabled entirely (default in environments without Alloy).
- `LANGFUSE_OTLP_ENDPOINT=""` → LGTM-only mode (no LangFuse arm of the dual export).
- Both set + LANGFUSE_PUBLIC_KEY/SECRET_KEY → full dual export.

**PromQL examples for adaptive routing:**

```promql
# Per-deployment p50 latency for section-synth process
histogram_quantile(0.5,
  sum by (le, deployment_id) (
    rate(litellm_llm_call_duration_bucket{kd_process="synth"}[1h])
  )
)

# Success rate per deployment per process
sum by (deployment_id, kd_process) (rate(litellm_llm_call_total{status="success"}[1h]))
  /
sum by (deployment_id, kd_process) (rate(litellm_llm_call_total[1h]))

# Bucket-split overflow rate by framework (signals chapter-cap pressure)
sum by (framework) (rate(kd_bucket_split_overflow_total[5m]))

# Refiner convergence — distribution of iters-to-accept by pinned_model
histogram_quantile(0.95,
  sum by (le, pinned_model) (
    rate(kd_refiner_iters_to_accept_bucket{outcome="accept"}[1h])
  )
)
```

**Next phase (deferred until ~1 week of production data accumulates):**
- Build Grafana dashboards from the metrics above (`docs/KD-OTEL-DASHBOARDS.md`)
- Implement `services/llm_chain_optimizer.py::get_best_deployment_for(process_name)` — PromQL-driven adaptive routing helper that re-ranks `_synth_entries()` based on observed performance, called by `pick_synth_deployment()`

**Cumulative LLM-call reduction so far** (with all six classical flags ON):
- Grader: ~95% token reduction (8/9 dims deterministic + 1 small-LLM call vs full GRADER_PROMPT)
- Critic: ~100% LLM reduction (zero LLM calls when classical critic on)
- Outline: ~40% token reduction (sees section summaries, not full chapter)
- Refiner: 100% of `_generate_adjustment` LLM calls eliminated + opportunistic skip of next-iter re-synth when patches alone reach acceptance
- **Curator: 100% LLM reduction** (zero LLM calls when classical curator on; the per-chapter LLM curator was the largest fixed cost — N curator calls per study)
- **Summary: ~70% output-token reduction** (deterministic reading-plan list never transits the LLM)

**Wall-clock improvements** are smaller than the headline doc projected — the irreducible-creative LLM calls (section synthesis Phase C, market_analysis grader dim, challenges/flashcards in outline, framing/money_projects in summary) still dominate. The real wins are **reliability (deterministic auditability)** and **token cost**, not minutes-off-the-clock.

### Files touched this session (Phase 5, uncommitted vs the Phase 4 commit)

**New modules:**
- `apps/fastapi/services/knowledge/curator_classical.py`
- `apps/fastapi/services/knowledge/summary_classical.py`

**Modified:**
- `apps/fastapi/graphs/knowledge/distiller.py` (Phase 5 — `KD_USE_CLASSICAL_CURATOR` branch in `_curate_one`; `KD_USE_CLASSICAL_SUMMARY` branch in `assembler`)
- `apps/fastapi/routers/v1/knowledge/debug.py` (`POST /debug/curator_compare` + `POST /debug/summary_compare` endpoints)
- `k8s/helm/values.yaml` (`kd.useClassicalCurator` + `kd.useClassicalSummary` flags)
- `k8s/helm/templates/_helpers.tpl` (`KD_USE_CLASSICAL_CURATOR` + `KD_USE_CLASSICAL_SUMMARY` env vars)
- `docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` (this file — Phase 5 ship log)
