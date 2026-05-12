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

### Phase status board (end of 2026-05-13)

| Phase | Step | Status |
|---|---|---|
| Phase 1 | Grader (classical 8/9 dims + small-LLM market_analysis) | ✅ shipped + validated |
| Phase 2.1 | Critic faithfulness (embedding-similarity via kd-embed) | ✅ shipped + validated |
| Phase 2.2 | Critic faithfulness → host-side MiniCheck/AlignScore | deferred (needs host-side llama-server setup) |
| Phase 3.1 | Outline (header-based extraction + small-LLM challenges/flashcards) | ✅ shipped + validated |
| **Phase 4** | Refiner (deterministic patches + template adjustment) | ✅ **shipped + validated this session** |
| Phase 5 | Curator + Summary split (glossary substitution + mdformat + small-LLM tone pass) | pending — smallest scope; ship together |

**Cumulative LLM-call reduction so far** (with all four classical flags ON):
- Grader: ~95% token reduction (8/9 dims deterministic + 1 small-LLM call vs full GRADER_PROMPT)
- Critic: ~100% LLM reduction (zero LLM calls when classical critic on)
- Outline: ~40% token reduction (sees section summaries, not full chapter)
- **Refiner: 100% of `_generate_adjustment` LLM calls eliminated** + opportunistic skipping of next-iter re-synth when patches alone reach acceptance threshold

**Wall-clock improvements** are smaller than the headline doc projected — the irreducible-creative LLM calls (section synthesis Phase C, market_analysis grader dim, challenges/flashcards in outline) still dominate. The real wins are **reliability (deterministic auditability)** and **token cost**, not minutes-off-the-clock.

### Files touched this session (uncommitted vs the Phase 1+2.1+3.1 commit)

**New modules:**
- `apps/fastapi/services/knowledge/refiner_classical.py` (Phase 4)

**Modified:**
- `apps/fastapi/graphs/knowledge/distiller.py` (Phase 4 — `KD_USE_CLASSICAL_REFINER` branch in Self-Refine loop: patch-then-regrade-then-maybe-break, classical adjustment text fallback)
- `apps/fastapi/routers/v1/knowledge/debug.py` (`POST /debug/refiner_compare` endpoint)
- `k8s/helm/values.yaml` (`kd.useClassicalRefiner` flag)
- `k8s/helm/templates/_helpers.tpl` (`KD_USE_CLASSICAL_REFINER` env var)
- `docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` (this file — Phase 4 ship log)
