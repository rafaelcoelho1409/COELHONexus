# Synth stage architecture — May 2026 SOTA (committed)

**Status:** committed design. Supersedes the 42-substep deprecated implementation in
`zdeprecated/apps/fastapi/graphs/knowledge/distiller.py` (`synthesize_chapter` lines 683-1983 +
`hierarchical_synth.py` Phase A/A.5/B/C/D + `helpers.py` 9-pass scrubber).

**Companion docs:**
- `PLANNER-ARCHITECTURE-2026-05-17.md` — upstream stage producing `plan-latest.json` that synth consumes
- `KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md` — authoritative-but-shallow map of the deprecated 42 substeps
- `KD-DOCKER-QUALITY-FINDINGS-2026-05-15.md` — defects this redesign structurally addresses

---

## Why redesign (not patch)

The deprecated synth stage is **Self-Refine (Madaan 2023) + Phase A/A.5/B/C/D + 9-pass regex scrubber + 8-dim weighted grader**. Every layer has been superseded in 2025-2026 literature:

- **Self-Refine is not SOTA in 2026** — [RefineBench (arXiv 2511.22173, Nov 2025)](https://arxiv.org/abs/2511.22173) shows even GPT-5/Gemini-2.5-Pro gain only **+1.8%** from unguided Self-Refine; DeepSeek-R1 **degrades -0.1%**. Compounds errors on long-form structured generation.
- **Phase A/A.5/B/C/D is a 2024 pattern** — [SurveyGen-I PlanEvo (arXiv 2508.14317, Aug 2025)](https://arxiv.org/abs/2508.14317) subsumes all of it with strictly stronger structural guarantees (+8.5% content quality, 3× citation density vs SurveyForge baseline).
- **9-pass regex scrubber is obsolete** — Pydantic schema + Instructor auto-retry on validation fail eliminates 7 of 9 passes. Output drift becomes structurally impossible, not regex-patched post-hoc.
- **8-dim weighted-score grader has biases** ([Hidden Shortcuts arXiv 2602.07996, Feb 2026](https://arxiv.org/abs/2602.07996)) — checklist-binary evaluators (RefineBench, Prometheus 2) win on Pearson human-agreement.

Every documented quality defect (`# docs:` leakage, `<code-ref/>` unresolved, orphan hex hashes, `(truncated)` visible, duplicate H2 sections, stub placeholders, ch02→ch04 mis-routing, silently-skipped chapters) is **structurally addressed** by the new architecture — not patched per-defect.

---

## The 9 substeps (replacing the deprecated 42)

```
1. cache_lookup        — Redis 30d, keyed by (plan_hash, tone_profile_hash, chapter_id)
2. corpus_normalize    — strip Mintlify/boundaries/meta at INGESTION (move from output-scrub)
3. outline_sdp         — dependency DAG with topological stage indexing (parallel within stage)
4. digest_construct    — per-source LLM digest → aggregate-merge; LLM picks WHAT goes WHERE
5. vault_sentinelize   — code blocks → <code-ref hash=...>; LLM never sees/emits code content
6. sawc_write          — stage-parallel best-of-N drafts via Instructor + Pydantic; writer ≠ critic (MAMM diversity)
7. checklist_eval      — ~10 binary criteria (Prometheus-2 rubric on free-tier model); failed items = guided feedback
8. mgsr_replan         — structured outline actions {merge|delete|rename|reorder|add} + CoRefine confidence-halting
   ↑──────── loop 6→7→8 until ≥80% criteria pass OR confidence plateau OR budget exhausted
9. render_audit_write  — Jinja render + round-trip code audit + 3 MinIO artifacts + Langfuse OTel span close
```

### Step-by-step detail

**1. `cache_lookup`**
- Redis key: `dd:synth:{framework}/{chapter_id}/{plan_hash}/{tone_hash}` TTL 30d
- Partial-cache (resume-on-failure): TTL 7d, keyed at iteration boundary, holds best-of-iter draft
- Unchanged from deprecated (caching pattern still SOTA)

**2. `corpus_normalize`**
- Strip Mintlify fence-meta (`theme=`, `expandable=`, `lines=`, etc.) at INGESTION
- Strip `--- docs-foo.md ---` raw-corpus boundaries at INGESTION
- Strip Mintlify orphan tags at INGESTION
- **Why move:** if the source markdown is clean before the LLM ever sees it, scrubber passes 0-2 become unnecessary post-hoc patches
- Replaces deprecated scrubber passes 0, 1, 2

**3. `outline_sdp` — Structure-Driven Planner (SurveyGen-I)**
- Single LLM call per chapter: outline = list of sections with dependencies between them
- Topological sort assigns each section a `stage_index` — same-stage sections execute in parallel; cross-stage runs sequentially with memory inheritance
- Replaces deprecated Phase A (outline), Phase A.5 (bucket split), hierarchical gate
- Bucket-split-when-overloaded behavior is now structural (DAG dependency), not heuristic
- Source: [SurveyGen-I arXiv 2508.14317 §3.1](https://arxiv.org/abs/2508.14317)

**4. `digest_construct` — LLM-assigned source-to-section routing (LLMxMapReduce-V3)**
- Per source doc: 1 LLM call produces a digest (summary + revision suggestions) keyed to outline section IDs
- Aggregate-merge-consolidate pass groups digests per section
- LLM reasons about WHICH source contributes WHAT to WHICH section — replaces blind embedding cosine
- Replaces deprecated Phase B (cosine hash routing) — root cause of ch02→ch04 mis-routing defects
- Source: [LLMxMapReduce-V3 arXiv 2510.10890 §3.2](https://arxiv.org/pdf/2510.10890)

**5. `vault_sentinelize` — Byte-exact code preservation (still SOTA)**
- Pre-process source: every ``` ```lang ... ``` ``` fence → `<code-ref hash="abc123"/>`, original stored in `{hash → text}` dict
- LLM never sees actual code content — hallucination structurally impossible
- Materialization (step 9): deterministic regex pass replaces sentinels with literal vault text
- **VeriCite-style audit** added: count `(missing, invented, duplicated, orphaned)` sentinel refs → feeds into checklist evaluator (step 7) as one binary criterion → ParetoBandit penalizes arms that hallucinate sentinel refs
- **Why still SOTA in 2026:** [Verbatim Data Transcription Failures arXiv 2601.03640 (Jan 2026)](https://arxiv.org/abs/2601.03640) shows SOTA models silently drop entries from long literal payloads — failure scales superlinearly with payload length. "Just trust the LLM to copy code verbatim" remains a bug factory on free-tier rotators.
- Tool-calling-quote alternative rejected: free-tier rotator arms vary wildly in function-calling fidelity; vault sentinels are model-agnostic plain-text and work identically across all arms.
- Constrained decoding alternative rejected: constrains STRUCTURE not arbitrary CONTENT — cannot guarantee byte-exact for opaque code strings.
- Sources: [VeriCite arXiv 2510.11394](https://arxiv.org/abs/2510.11394), [Citation-Grounded Code Comprehension arXiv 2512.12117](https://arxiv.org/abs/2512.12117)

**6. `sawc_write` — Structure-Aware Writing Controller (SurveyGen-I + MAMM diversity)**
- Stage-parallel execution: all sections at the same DAG stage write concurrently; cross-stage waits inherit memory ledger
- Per section: **best-of-N drafts** (N=3) from 2 distinct rotator picks (writer ≠ critic for diversity per [MAMM-Refine arXiv 2503.15272](https://arxiv.org/pdf/2503.15272))
- Output schema: Pydantic-validated via [Instructor](https://github.com/instructor-ai/instructor) (auto-retry on validation fail)
  ```python
  class Chapter(BaseModel):
      sections:   list[Section]
      challenges: list[Challenge]
      flashcards: list[Flashcard]
      citations:  list[Citation]
  class Section(BaseModel):
      heading:    str
      paragraphs: list[str]      # join at render (kills literal \n\n bugs)
      code_refs:  list[CodeRef]  # typed list — can't drop/duplicate accidentally
  ```
- Replaces deprecated Phase C (per-section synth), Phase D (merge), scrubber passes 3-7
- Source: [SurveyGen-I SAWC §3.2](https://arxiv.org/abs/2508.14317), [MAMM-Refine §4](https://arxiv.org/pdf/2503.15272)

**7. `checklist_eval` — Binary checklist evaluator (RefineBench + Prometheus 2)**
- ~10 binary criteria per chapter, Prometheus-2-style rubric prompt evaluated on a free-tier model
- Examples: `has_intro_paragraph`, `all_code_refs_resolved`, `cites_at_least_3_sources`, `no_stub_placeholders`, `headings_unique`, `prose_chars_within_bounds`, `no_orphan_fences`, etc.
- Pass = >80% criteria met
- Failed criteria → rephrased as natural-language instructions, fed to step 8 as guided-refinement signal
- Replaces deprecated 8-dim weighted grader + deterministic pre-gates + decision_logic enum
- Sources: [RefineBench arXiv 2511.22173 §3](https://arxiv.org/abs/2511.22173), [Prometheus 2](https://www.researchgate.net/publication/386192699)
- Deterministic pre-gates (min/max chars, fence balance, manifest validates) **kept** as fast-fail before LLM eval — they're free

**8. `mgsr_replan` — Memory-Guided Structure Replanner + CoRefine halting**
- Between iterations, MGSR LLM emits structured replan actions on the outline DAG:
  ```json
  {"action": "merge|delete|rename|reorder|add",
   "targets": ["section_id_A", "section_id_B"],
   "rationale": "..."}
  ```
- Replaces deprecated free-form `ADJUSTMENT_PROMPT` + Phase-4 classical patches
- **CoRefine confidence-guided halting** ([arXiv 2602.08948](https://arxiv.org/pdf/2602.08948)): halt when MGSR confidence on "no further actions needed" stabilizes
- Replaces deprecated OP-7 regression-early-stop (issue-count delta) with principled confidence stopping
- Loop step 6→7→8 until: ≥80% criteria pass OR confidence plateau OR budget exhausted (default budget: 5 iters per chapter)
- **OP-12 best-seen rescue kept** but renamed: `argmax(checklist_pass_rate)` not `argmax(weighted_score)`; commits best iteration if budget exhausted
- **OP-19 exception rescue dropped** in favor of Instructor's structured retry + ParetoBandit fallback rotator

**9. `render_audit_write` — Materialize + audit + persist**
- Jinja2 template renders Pydantic `Chapter` → markdown (single deterministic pass, replaces 9-pass scrubber)
- Replace `<code-ref hash=...>` sentinels with literal vault text
- Round-trip audit: re-hash every materialized code block, assert byte-identical to vault → on any drift, structured retry
- Write 3 MinIO artifacts: `chapter{NN}/README.md`, `chapter{NN}/challenges.md`, `chapter{NN}/flashcards.json`
- Close Langfuse OTel span (per-chapter Gantt automatically derives from spans)

---

## Defect → fix mapping (every documented defect structurally prevented)

| Defect today (KD-DOCKER-QUALITY-FINDINGS) | Root cause in deprecated | Structural fix in new arch |
|---|---|---|
| `# docs:` source-ID leakage in prose | regex scrubber pass 5 brittle | Citations are typed Pydantic field `list[Citation]` — can't appear in prose |
| `<code-ref/>` unresolved (vault routing miss) | Phase B cosine routing miss | Digest construction (4) uses LLM-assigned routing with reasoning |
| Orphan hex hashes | regex passes 3-4 miss edge cases | Instructor structured emission makes malformed output impossible |
| `(truncated)` markers visible to readers | scrubber pass 8 marks instead of retries | Round-trip audit (9) triggers structured retry, not annotation |
| Duplicate H2 sections | Phase D merge has no dedup | MGSR (8) emits `merge` action between stages on duplicate detection |
| Stub placeholders ("TODO", "..." etc.) | structured-output silently fails | Instructor auto-retries on Pydantic validation fail; `no_stub_placeholders` is a checklist criterion |
| ch02 contains ch04 content | no cross-chapter memory | CaM-Writing memory ledger between stages; MGSR `reorder` action |
| 3 chapters silently skipped | OP-19 catches + swallows exceptions | OTel span errors surface; Instructor retry → fail loud, not silent |

---

## Free-tier rotator allocation ($0 cost)

Maps cleanly onto existing ParetoBandit + LiteLLM rotator (`services/llm/chain.py`):

| Role | Pool | Why |
|---|---|---|
| **Writer** (large-context, prose strength) | `glm-4.6`, `qwen-3-coder-30b`, `llama-4-scout` | High-context, varied training distributions |
| **Critic** (must differ from writer arm — MAMM diversity) | `deepseek-v4-flash`, `gemini-2.5-flash` | Different families → less correlated errors |
| **Checklist evaluator** | `gemini-2.5-flash` | Strong on structured output + fast |
| **Digest LLM** | `deepseek-v4-flash` | Cheap, high-throughput, parallel across N sources |
| **MGSR replanner** | `glm-4.6` or `qwen-3-coder-30b` | Strong structured-output models for typed action emission |

Per `feedback_kd_quality_over_speed.md`: tokens are free, runtime isn't a concern. The new pipeline burns more LLM calls per chapter (N=3 best-of-N drafts × stages × refinement iters × checklist eval × MGSR replan) for measurably better output quality.

---

## What survives from the deprecated impl

| Deprecated component | Status | Reasoning |
|---|---|---|
| Cache lookup (Redis 30d + partial 7d) | KEEP | Caching pattern still SOTA |
| Vault sentinelization | KEEP + AUGMENT | Still SOTA byte-exact (per Verbatim 2601.03640); add VeriCite audit |
| Per-section parallelism | KEEP shape, REPLACE mechanism | Asyncio.gather → DAG stage-indexing (SAWC) |
| OP-12 best-seen rescue | KEEP, renamed | Generalizes to `argmax(checklist_pass_rate)` |
| Deterministic pre-gates (min/max chars, fences) | KEEP | Free fast-fail before LLM evaluator |
| Tone profile | KEEP | Framework-specific style guide unchanged |
| Chapter model pin | KEEP | Style consistency across iterations within a chapter |
| Prose-only short-circuit (OP-46) | KEEP | When vault empty, skip Self-Refine machinery entirely |

## What dies completely

| Deprecated component | Killed by |
|---|---|
| Self-Refine loop as orchestrator | Guided-refinement on checklist (RefineBench evidence) |
| Phase A/A.5 bucket split (separate step) | SDP dependency DAG (subsumes both) |
| Phase B cosine hash routing | Digest construction LLM-assigned routing |
| Phase D merge (no dedup) | MGSR `merge` action |
| 7 of 9 scrubber passes | Pydantic schema + Instructor validation |
| 8-dim weighted grader | Binary checklist evaluator |
| `ADJUSTMENT_PROMPT` (free-form text) | Typed MGSR replan actions |
| OP-7 regression early-stop | CoRefine confidence-guided halting |
| OP-19 exception rescue | Instructor retry + ParetoBandit fallback |
| Phase-4 classical patches | MGSR structured actions |

---

## Observability shape

- **Langfuse OSS + OTel** (already in stack via `services/llm/otel_setup.py`) — every PlanEvo stage = one OTel span = free Gantt timeline per chapter
- **Per-chapter live UI tabs**: each chapter's Self-Refine substitute (steps 6→7→8) streams SSE events to FastHTML; pattern mirrors planner page
- **Per-iter score trajectory**: derived from checklist pass-rate (not weighted score), one data point per iteration
- **MGSR replan actions** streamed live to UI as discrete events — visible decision log of what the replanner did between iterations
- **Bandit telemetry**: per-arm checklist pass rates feed back into ParetoBandit reward — arms that produce drift-free outputs get more traffic

## FastHTML page shape (mirrors planner pattern)

- Top: KPI grid (chapters in/out, iterations consumed, checklist criteria met, wall time)
- Middle: per-chapter tabs (5+ parallel), each showing:
  - Current stage in the SDP DAG
  - Per-iter checklist pass rate timeline
  - Live MGSR replan actions as they fire
  - Final rendered chapter preview
- Bottom: scrubber-residual counters (post-render audit dimensions) for the rare passes still needed

3× JS complexity vs planner page, 2× FastAPI SSE event surface. Per the user's [feedback_terse_responses.md], the UI itself stays clean — depth is in collapsible per-chapter sections.

---

## Implementation order (when synth ships)

1. **`corpus_normalize`** (step 2) — easiest, do at ingestion-time refactor; immediate quality win
2. **`vault_sentinelize`** (step 5) — port from deprecated `helpers.py:_vault_code_blocks` + add VeriCite audit counters
3. **`outline_sdp`** (step 3) — single LLM call per chapter; foundation for everything downstream
4. **`digest_construct`** (step 4) — fixes ch-mis-routing root cause; high-value mid-priority
5. **`sawc_write`** (step 6) — heaviest, builds on SDP DAG; Instructor schema is the key dependency
6. **`checklist_eval`** (step 7) — independent of writer, can be developed parallel to 6
7. **`mgsr_replan`** (step 8) — orchestrates the loop; depends on 6 + 7
8. **`render_audit_write`** (step 9) — final node, mostly deterministic
9. **`cache_lookup`** (step 1) — wire last (defense-in-depth on top of a working pipeline)

Per node added: append to `IMPLEMENTED` tuple in `synth/graph.py`, add `SUBSTEP_RENDERERS[idx]` in `apps/fasthtml/static/js/docs_distiller.js`.

---

## Key source references

| Paper | Role in new architecture | arXiv / DOI |
|---|---|---|
| **SurveyGen-I** | PlanEvo (SDP + SAWC + MGSR) — architectural backbone | [2508.14317](https://arxiv.org/abs/2508.14317) |
| **LLMxMapReduce-V3** | Digest construction pattern | [2510.10890](https://arxiv.org/pdf/2510.10890) |
| **MAMM-Refine** | Multi-agent writer ≠ critic diversity | [2503.15272](https://arxiv.org/pdf/2503.15272) |
| **RefineBench** | Empirical case AGAINST Self-Refine; binary checklist evaluator pattern | [2511.22173](https://arxiv.org/abs/2511.22173) |
| **CoRefine** | Confidence-guided halting (replaces OP-7) | [2602.08948](https://arxiv.org/pdf/2602.08948) |
| **Prometheus 2** | Open-source rubric evaluator (Pearson 0.897 with humans) | [arxiv preprint](https://www.researchgate.net/publication/386192699) |
| **VeriCite** | Citation-grounded preservation pattern endorsement | [2510.11394](https://arxiv.org/abs/2510.11394) |
| **Citation-Grounded Code Comprehension** | Code-doc-domain validation of preservation patterns | [2512.12117](https://arxiv.org/abs/2512.12117) |
| **Verbatim Data Transcription Failures** | Empirical case AGAINST "just trust the LLM to copy code" | [2601.03640](https://arxiv.org/abs/2601.03640) |
| **Hidden Shortcuts in LLM Eval** | Cautionary tale on weighted-score grader biases | [2602.07996](https://arxiv.org/abs/2602.07996) |
| **Reasoning on a Budget** | Adaptive compute survey supporting best-of-N + targeted refinement over long sequential loops | [2507.02076](https://arxiv.org/html/2507.02076v1) |
| **Self-Refine** (baseline) | Original 2023 method; documented superseded by guided-refinement | [2303.17651](https://arxiv.org/abs/2303.17651) |
| **Instructor** | Pydantic-validated structured output with auto-retry | [github](https://github.com/instructor-ai/instructor) |
| **Outlines** | FSM-constrained decoding (lower hallucination 1.8%, requires direct model access) | [github](https://github.com/dottxt-ai/outlines) |
| **Langfuse** | OSS LLM observability (OpenTelemetry-native) — already in stack | [docs](https://langfuse.com/docs/observability/overview) |

## Footnotes — what's NOT in scope (deferred)

These would be marginal-gain improvements; documented so we don't reinvent them when they come up:
- **DSPy-MIPRO program optimization** — could tune prompts automatically; defer until baseline ships and we have ParetoBandit reward signal stable
- **Constitutional-AI style multi-principle critique** — could replace checklist with a principles tree; checklist is simpler and the empirical evidence (RefineBench) is direct
- **AST-level code grounding** — overkill for documentation; not in any doc-synth literature
- **CodeAct executable feedback** — useful for code generation, not prose synthesis
- **Cross-encoder rerankers for digest aggregation** — could improve step 4; defer until digest construction proves to be the bottleneck
- **Full DSPy migration** — Instructor + LiteLLM + ParetoBandit already covers the structured-output + routing surface
