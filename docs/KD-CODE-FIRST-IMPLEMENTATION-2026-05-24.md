# Code-First Distillation Implementation Roadmap (2026-05-24 evening)

Empirical state + SOTA research synthesis after the FastMCP chapter-1 run that produced **0 code fences in 62 KB of prose** despite all earlier code-first ships.

**Cross-references:**
- [`KD-CODE-FIRST-SOTA-2026-05-24.md`](./KD-CODE-FIRST-SOTA-2026-05-24.md) — original SOTA audit; this doc supersedes its priority order
- [`KD-SYNTH-SOTA-2026-05-24.md`](./KD-SYNTH-SOTA-2026-05-24.md) — the 7 ships landed before this

## 1. Empirical root cause (not what I thought)

I assumed SAWC writer was the bottleneck. The data shows the opposite — **`digest_construct` is the gate that's closed**.

| Layer | Status | Evidence |
|---|---|---|
| Vault loaded into SAWC | ✅ Working | 1499 entries visible across 227 sources |
| SAWC writer uses what's given | ✅ ~100% utilization | s1: 7/7, s10: 2/2, s15: 5/6, s13: 2/2 |
| **Digest routes hashes per section** | **❌ Severely under-routing** | 21 total hashes across 20 sections; **12 sections got 0** |
| Render materializes from vault | ✅ Fixed last turn (not yet deployed) | runtime-sentinelize fallback added to `_load_per_source_vaults` |

### Per-section data from chapter 1

| section | digest routed | sawc emitted | utilization |
|---|---|---|---|
| s1  | 7 | 7 | 100% |
| s2-s9 (except s5) | 0 | 0 | n/a (no bank) |
| s5  | 1 | 1 | 100% |
| s10 | 2 | 2 | 100% |
| s13 | 2 | 2 | 100% |
| s15 | 6 | 5 | 83% |
| s17 | 1 | 1 | 100% |
| s19 | 1 | 1 | 100% |
| s20 | 1 | 1 | 100% |
| others (12 sections) | 0 | 0 | n/a |

**SAWC writer is faithful. Digest is starving SAWC.**

## 2. Why digest under-routes

Digest's prompt asks the LLM to identify, per source, which (section_id, code_refs[]) tuples this source contributes. The LLM is free to emit `code_refs: []` per contribution — no hard floor, no schema enforcement. Sources without obvious section-relevant code blocks get empty arrays; the LLM bias toward empty-when-uncertain compounds across 252 sources → most sections receive zero.

## 3. Top 3 SOTA approaches (May 2026), ranked by ROI

### #1 — Mandatory tool-call Curator + locked-slot Commentator (highest ROI; structural fix)

Source: [Citation-Grounded Code Comprehension (arXiv 2512.12117)](https://arxiv.org/abs/2512.12117) + production primitives (`tool_choice="required"` for OpenAI-compat, `tool_config: ANY` for Gemini, `tool_choice="any"` for Mistral).

**Mechanism**: Replace SAWC's single per-draft prompt with two passes:
1. **Curator** — `tool_choice="required"` on `pick_canonical_examples(picks: list[CodePick])`. Provider rejects any plain-text reply. The LLM cannot return prose; it must emit ≥4 picks.
2. **Commentator** — receives the picked code rendered as `<slot id=N>` blocks in the prompt. Schema is `{slot_id: {before, after}}` — paragraphs only. The LLM cannot place code; the slots are already there.

**Cost**: 2 calls per section (instead of 3 best-of-N + critic). Net latency neutral if N=3 was already in use.

**Impact**: Sections at 0 refs become impossible by construction. Floor is enforced by the API surface, not by Pydantic repair (which empirically fails — 9 repairs fired on chapter 1 and most sections still emitted 0).

**Free-tier coverage** (2026):
- Mistral: `tool_choice="any"` enforces at least one tool call ([docs](https://docs.mistral.ai/capabilities/function_calling))
- NIM: `tool_choice` with named function ([docs](https://docs.nvidia.com/nim/large-language-models/latest/function-calling.html))
- Gemini: `tool_config.function_calling_config.mode: ANY` ([docs](https://ai.google.dev/gemini-api/docs/structured-output))
- DeepSeek / Cerebras / Groq: OpenAI-compatible `tool_choice`

**Quirks**: Gemini's `structured_output + tool_calling` in the same call has a known bug ([issue #2257](https://github.com/openai/openai-agents-python/issues/2257)). Workaround: two separate calls (one with `response_format`, one with `tool_choice`). Two-pass architecture already implies separate calls — no problem.

### #2 — Augment SAWC's per-section bank from chapter-wide vault (lower-effort floor-raiser; ship NOW)

When digest routes < 6 hashes to a section, **pad with pedagogically-ranked hashes from the chapter-wide vault**. The chapter-wide bank is already loaded as `vault_rich` (1499 entries for FastMCP) — just not used because SAWC was honoring digest's narrow routing.

**Algorithm**: After computing `allowed_hashes_set` from `contributions`, if `len(allowed_hashes_set) < 6 and vault_rich`, score the chapter-wide vault via `rank_hashes_by_pedagogy()` (already shipped), then add the top-20 hashes not already in `allowed_hashes_set`.

**Cost**: 0 LLM calls. Pure Python ranking. ~20 LOC in sawc_write.

**Impact**: Every section gets ≥6-20 candidate hashes regardless of digest's behavior. The LLM cannot emit 0 refs from a non-empty bank without violating the existing density floor.

**Risk**: padded hashes may not be semantically relevant to the specific section. Mitigation: the pedagogy ranker prefers small canonical examples; combined with the LLM picking 3-6 from 20+, irrelevant additions get ignored.

### #3 — GEPA Reflective Prompt Evolution on the SAWC/Digest prompts (medium ROI; defer)

Source: [GEPA (Agrawal et al., ICLR 2026 Oral, arXiv 2507.19457)](https://arxiv.org/abs/2507.19457). +13% over MIPROv2, +20% over GRPO with 35× fewer rollouts. Built into DSPy.

**Mechanism**: GEPA reads execution traces, reflects on WHY the LLM ignored 80% of available hashes, rewrites the prompt. Use 10-20 hand-graded "good chapter" examples.

**Cost**: ~50-100 LLM calls one-time per pipeline change. Free-tier compatible.

**Why defer**: requires manual labeling of "good" exemplars. Ship #1 + #2 first; if density still hits a ceiling, GEPA can squeeze the last 5-10pp.

## 4. Radical alternative — API-graph-first synthesis (research preview)

Two May 2026 papers point at a fundamentally different paradigm:
- [SurveyG (arXiv 2510.07733)](https://arxiv.org/abs/2510.07733) — hierarchical citation graph drives outline
- [SurGE / SIGIR 2026](https://github.com/oneal2000/SurGE) — evidence-first outline pattern

Adapted: extract an API-symbol graph from the vault (nodes = API names like `FastMCP`, `@mcp.tool`; edges = co-occurrence in code blocks). Anchor each section to a connected subgraph. The planner walks the graph instead of generating an abstract outline. Each section has its canonical examples **structurally pre-bound** — no digest routing decision needed.

**Defer for now.** Requires re-architecting the planner. Phase 2 work.

## 5. Concrete ship — what lands in this commit

| Ship | Description | LOC | Layer |
|---|---|---|---|
| **A** | **Augment SAWC's per-section bank** from chapter-wide vault when digest under-routes. Threshold: `len < 6`. Pad with top-20 pedagogically-ranked hashes. | ~20 | `sawc/node.py` |
| **B** | **Tighten SAWC writer prompt** density rules — make minimum code_refs a function of bank size (≥3 if bank ≥10, ≥6 if bank ≥20). | ~10 | `sawc/service.py` |
| **C** | **Sharper code-density violator feedback** — when validator triggers repair, include "The bank had N hashes but you only emitted M. List the FULL bank below and pick at least F:" + enumerate the available hashes again in the repair message. | ~15 | `sawc/service.py` |

**Total: ~45 LOC**.

**Phase 2 (next session, after empirical validation)**: Curator→Commentator two-pass architecture (~200 LOC) + bandit-reward code-density signal (~30 LOC).

## 6. Concrete kitchen-sink solution (Phase 2 sketch — NOT shipping this turn)

```
┌──────────────────────────────────────────────────────────┐
│ Per-Section Pipeline (PHASE 2)                           │
├──────────────────────────────────────────────────────────┤
│ 1. Augmented bank — combine digest-routed + pedagogy-     │
│    ranked chapter-wide additions (THIS SHIP — Phase 1)   │
│                                                          │
│ 2. CURATOR call: tool_choice="required" with 4-6 slot    │
│    pick_canonical_examples schema                        │
│                                                          │
│ 3. RENDER picks into commentator user-message with       │
│    `<slot id=N role=primary>{code_body}</slot>` blocks   │
│                                                          │
│ 4. COMMENTATOR call: schema {slot_id: {before, after}}.  │
│    LLM cannot place code, only prose-around-slots        │
│                                                          │
│ 5. POST-PROCESS — scan paragraphs for inline backtick    │
│    API mentions; inject canonical example if missing     │
│    from nearby code blocks                               │
│                                                          │
│ 6. BANDIT REWARD adds γ × (citations_used/hashes_avail)  │
│    so FGTS-VA naturally prefers arms that emit more code │
└──────────────────────────────────────────────────────────┘
```

## 7. What's NOT shipping and why

| Technique | Why skip |
|---|---|
| **Skeleton-of-Thought** | Designed for latency; doesn't address prose bias. Already approximated by N=3 + tournament. |
| **Instructor reask alone** | Already in use via Pydantic repair loop; empirically fails (9 repairs on chapter 1 → 12 sections still at 0). Reask alone doesn't fix the architectural bias. |
| **Fine-tuning / DPO** | Violates "no fine-tuning" constraint. GEPA (#3) outperforms RL with 35× fewer rollouts per ICLR 2026. |
| **Watermarking / code provenance** | Wrong problem — those track LLM-generated code, not docs-→-distillation. |

## 8. Acceptance criteria for Phase 1 ships (next FastMCP rerun)

- Chapter 1 should have ≥**80 code_refs** total across 20 sections (avg 4/section, up from 1.0)
- Sections at 0 code_refs: ≤2 (down from 12)
- README.md size: 4-8 KB per chapter (down from 62 KB prose-only)
- Code line ratio: ≥40%
- Code fences per chapter: ≥40 (up from 0)

If these don't hit on the next run, Phase 2 (Curator→Commentator) ships immediately.

## Sources

- [Citation-Grounded Code Comprehension (arXiv 2512.12117, Dec 2025)](https://arxiv.org/abs/2512.12117) — citation-grounded enforcement
- [GEPA: Reflective Prompt Evolution (arXiv 2507.19457, ICLR 2026 Oral)](https://arxiv.org/abs/2507.19457)
- [SurveyG (arXiv 2510.07733)](https://arxiv.org/abs/2510.07733)
- [Deterministic AST Hallucination Correction (arXiv 2601.19106)](https://arxiv.org/html/2601.19106v1)
- [Mistral function calling docs](https://docs.mistral.ai/capabilities/function_calling)
- [NIM tool calling docs](https://docs.nvidia.com/nim/large-language-models/latest/function-calling.html)
- [Gemini tool_config ANY mode](https://gemilab.net/en/articles/gemini-api/gemini-api-tool-config-mode-control)
- [DSPy GEPA tutorial](https://dspy.ai/tutorials/gepa_ai_program/)

## Empirical note

I could not find published evidence that Anthropic Cookbook, fly.io, or Stripe-docs style code-dense content is generated via LLMs in production — those appear human-authored with LLM assist. **The May 2026 frontier (SurveyG, SurGE, GEPA, citation-grounded comprehension) is academic**; no public production system has solved "200-page framework doc → code-dense distillation". You'd be at the bleeding edge.
