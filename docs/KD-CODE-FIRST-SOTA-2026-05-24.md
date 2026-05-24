# Code-First Distillation SOTA + Planner/Synth Direction Audit (2026-05-24)

User's core goal restated: ingest 200-400 pages of framework docs → produce a concise CODE-RICH learning artifact that conveys ~90% of the framework's actionable value at ~10% of the page count. Less prose, more code, organized by pedagogical role. **Empirically, today the pipeline produces the opposite.**

**Cross-references:**
- [`KD-SYNTH-SOTA-2026-05-24.md`](./KD-SYNTH-SOTA-2026-05-24.md) — the 5 SOTA improvements already ranked (pairwise picker, LLM-judge faithfulness, book_harmonize, mgsr loop, adaptive checklist)
- [`KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md`](./KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md) — the original "vault sentinelization" decision (was correct for May 2024, becomes a bottleneck for code-first goal)
- [`SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`](./SYNTH-ARCHITECTURE-SOTA-2026-05-18.md) — committed 6-node Synth architecture

## 1. Empirical evidence — Chapter 1 of FastMCP (just generated)

| Metric | Result |
|---|---|
| File | `synth/fastmcp/ch-01-fastmcp-server-applications/README.md` |
| Total chars | 83,112 |
| Triple-backtick fences | **0** |
| Code blocks | **0** |
| Audit status | `audit_passed=True` (the audit only checks "do claimed hashes resolve" — zero claims = trivially pass) |
| Render telemetry | `0/0 code_refs resolved, 0 missing, 0 drift, 0 sentinels left` |

The chapter reads as a long architectural summary with section-end "Sources for this section" lists. **For someone wanting to learn how to USE FastMCP, this is near-useless.** They need decorator examples, server setup snippets, transport configs — not paragraphs describing them.

## 2. Root cause — the vault sentinelization hides code from the LLM

The pipeline architecture today:

```
Ingestion              Code fence in source → sha256[:16] hash → vault.json
                       Source markdown gets <code-ref hash="abc123"/> SENTINEL

Synth outline/digest   LLM sees SENTINELIZED markdown — opaque hash IDs only

Synth sawc_write       LLM prompt: "Pick from these allowed_hashes:
                       [abc123, def456, …]. Cite via code_refs field."

Synth render           Vault lookup materializes hashes → final markdown
```

The fatal property: **the LLM never sees actual code during writing.** It only sees 16-char hex IDs. To "cite" a hash, the LLM has to:
- Trust that hash `abc123` is "the canonical @mcp.tool example"
- Pick from `allowed_hashes` based on *adjacent prose context only*
- Have no way to evaluate which code is pedagogically most valuable
- Have no way to *generate* new derived code

Empirically: when the model is uncertain about which hash is the right one (always — because hashes are opaque), it picks zero. Result: prose-only chapters.

The original design rationale ([`KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md`](./KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md)) was **valid in May 2024**: Verbatim arXiv 2601.03640 showed free-tier LLMs hallucinate when reproducing literal code in long contexts. The vault was a defense against that. But it over-rotated — it solved hallucination by making code invisible, instead of making code visible-but-verified.

## 3. Three parallel research agents converged on the same architectural fix

### Convergence point: **"Visible Vault"** — show the LLM the code, re-substitute at render time

All three independent research agents (code-first SOTA, hybrid vault, derived-code SOTA) converged on the same pattern. Key papers:

| Paper / Source | Contribution |
|---|---|
| **arXiv 2601.03640 (Jan 2026)** — Verbatim Data Transcription Failures | Prescription: "LLM as planner, deterministic tools for emission." Don't trust the LLM to reproduce code byte-perfect; let it *plan around* code while a renderer guarantees fidelity. |
| **arXiv 2604.18170 (Apr 2026)** — Copy-as-Decode grammar | Introduces `<copy id="..."/>` + `<gen>...</gen>` typed markers. Confirms markered approach is SOTA. (Constrained-decoding implementation skipped — needs logit access.) |
| **Deterministic Quoting (Yeung 2025) + VerbatimRAG (KR Labs ACL BioNLP 2025) + CogCanvas (arXiv 2601.00821)** | The healthcare/legal industry pattern: LLM sees code AND emits envelope with stable id; renderer **discards LLM's body** and substitutes vault[id] verbatim. LLM's copy is *advisory*. |
| **arXiv 2505.18128 (Frankentext)** | 90% verbatim constraint produces MORE original and coherent narrative than free generation — the constraint paradoxically improves quality. Apply: target ≥40% code lines per chapter. |
| **Diátaxis (diataxis.fr) + Anthropic Cookbook (80+ notebooks)** | The pedagogical reference for code-first structure. 4 quadrants (tutorial / how-to / reference / explanation). Notebook format empirically enforces >50% code-to-prose. |
| **Yasunaga et al. ICLR 2024 — Analogical Prompting** + **arXiv 2309.17272 (MPSC)** | The mechanism for derived-code generation: condition LLM on the canonical pattern + target domain → emit adapted example. Multi-Perspective Self-Consistency verifies API parity. |
| **TOCE 2025 (10.1145/3732791)** — Worked-example effect | Sweller's cognitive-load theory: 1 verbatim + 1 derived per concept is the novice sweet spot. NOT 3-5 derivations (overloads). |

## 4. Architectural delta — what changes vs what stays

### What STAYS (correct in current direction)

| Component | Why it stays |
|---|---|
| 8-node Planner pipeline | Clusters 200-400 docs into 5-10 balanced chapters — sound foundation |
| Vault byte-perfect storage | Solves hallucination by deterministic re-substitution at render time |
| Per-chapter SAWC stage-DAG | Parallel section writing within stages is the right shape |
| Checklist + book_harmonize | Already shipped, doesn't conflict |
| FGTS-VA bandit rotator | Independent of code-first concerns, working |
| Free-tier API only | Constraint preserved across all proposed changes |

### What CHANGES (the code-first re-architecture)

| Where | Change | LOC est |
|---|---|---|
| **Ingestion** | Vault still hash-keyed, but ALSO compute an AST-normalized fingerprint (per-language, drops comments/whitespace) for fuzzy-similarity audit. ~20 LOC added. | +20 |
| **NEW node: `code_inventory`** | After Planner, before Synth. Scores every code block in the chapter's docs for pedagogical priority: canonical (smallest runnable form) / primitive (isolates one API) / recipe (solves a task) / counter (anti-pattern). Heuristics: LOC sweet spot 5-30, comes from tutorial/quickstart page boosts score, API-name match with chapter title boosts, self-contained imports boost. | +120 |
| **Synth `digest_construct` (modified)** | Per-section, pick top-K code blocks from inventory. Inject them INTO the prompt as visible code envelopes — `<code id="abc" lang="python" source="...">{actual_code}</code>` — NOT as opaque hashes. | +60 |
| **Synth `sawc_write` (modified)** | New prompt: HARD CONSTRAINTS — ≥50% code lines, prose chunks 1-3 sentences ≤120 words, follow Diátaxis section template (At-a-glance → Minimal working example → Building blocks → Patterns → Gotchas). LLM reproduces `<code id="..."/>` envelope from the bank verbatim (or emits ` ```python derived` for adapted code). | +80 |
| **NEW node: `sawc_derive`** (OPTIONAL, can be Phase 2) | After `sawc_write`, before `checklist_eval`. Walks `<!--DERIVE: pattern=X, target_domain=Y-->` markers, emits 1 derived example per marker via Analogical Prompting + MPSC vote (N=3, tree-sitter API parity check). | +150 |
| **Synth `render_audit_write` (modified)** | Two-stage: (a) extract every `<code id="..."/>` from draft, look up vault[id], compute AST-normalized similarity, classify verbatim (≥0.92) / derived (0.55-0.92) / hallucinated (<0.55). (b) ALWAYS replace verbatim+hallucinated with `vault[id]` (deterministic quoting). Derived blocks pass through unaudited. Enforce density gate: reject if code-line ratio <0.40 → fail back to mgsr loop. | +90 |
| **Synth `checklist_eval` (1 new criterion)** | Add `code_density_appropriate` to the binary checklist. Threshold: ≥40% code lines + ≤2 sentences between blocks. | +30 |

**Total new LOC**: ~430 (without `sawc_derive`), ~580 (with `sawc_derive`).

### Vault is preserved, evolved, not removed

The vault was right in design (deterministic fidelity) but wrong in interface (opaque hashes). The fix is keep the storage backend, change the interface:

| Aspect | Today (opaque vault) | Code-first (visible vault) |
|---|---|---|
| Storage | `vault.json` keyed by sha256[:16] | **Same** |
| What the LLM sees | `<code-ref hash="abc123"/>` placeholder | `<code id="abc123" lang="python" source="...">{actual code}</code>` envelope |
| What the LLM emits | `code_refs: ["abc123"]` field | The same envelope verbatim OR ` ```python derived ` for adapted code |
| Render-time substitution | Look up hash → insert verbatim | Look up hash → **discard LLM's body** → insert vault[id] verbatim (deterministic quoting) |
| Hallucination defense | LLM can't see code → can't corrupt | LLM CAN see, but renderer discards LLM's body for verbatim citations — fidelity is **algorithmic**, not promised |
| Code blocks in final output | Empirically 0 (LLM picks no hashes) | Expected ≥40-50% line density (enforced by checklist + density gate) |
| LLM can generate derived code | ❌ Architecturally impossible | ✅ Via ` ```python derived ` blocks + optional `sawc_derive` MPSC node |

## 5. Proposed CODE-FIRST chapter format (Diátaxis-derived)

```
# Chapter N: <Concept>

## At a glance  ← Diátaxis "explanation"
[1 paragraph, ≤80 words: what this is, when to reach for it, what it replaces]

## Minimal working example  ← Diátaxis "tutorial"
<one verbatim canonical block, smallest runnable form>
→ 1 anchor sentence: "This is the smallest thing that runs."

## Building blocks  ← Diátaxis "reference" (3-6 sub-sections)

### <Primitive 1>
<verbatim block, ≤30 lines>
<1-2 prose lines explaining the non-obvious bits>

### <Primitive 2>
…

## Patterns  ← Diátaxis "how-to" (2-4 task recipes)

### How to: <task>
<verbatim block solving the task, OR derived adaptation>
<≤3 lines commentary>

## Adapting the patterns  ← derived code (OPTIONAL — sawc_derive)
[Each derived example pairs with the canonical it adapts from. Bold callout
sentence explains: "Notice the structural parity: X. What changed: Y."]

## Gotchas  ← anti-patterns
- `bad_call()` raises X because Y — use `good_call()` instead.

## Cross-references
- See Chapter M for <related concept>
- Upstream docs: <slug>/pages/<id>
```

Density target: ≥40% lines are code, ≤2 sentences between blocks, ≤120 words per prose chunk. The constraint is what produces the "concise code-rich" feel.

## 6. Priority-ranked ship list (this work, in order)

| Rank | Change | LOC | What it unblocks | Dependencies |
|---|---|---|---|---|
| 🥇 **1** | **Visible Vault** — switch sentinels from opaque hashes to full envelopes with code body inside. Modify digest_construct's prompt assembly + sawc_write's writer prompt + render_audit_write's substitution+audit. | ~230 | The single most important fix. Without this, every other code-first improvement is futile. | None |
| 🥈 **2** | **`code_inventory` node** — pedagogical priority scoring of vault entries (canonical / primitive / recipe / counter-example). | ~120 | Lets the digest pick the BEST code per section, not just "any 12 blocks". | Ship #1 |
| 🥉 **3** | **Code-density checklist criterion + checklist gate** — adds `code_density_appropriate` to the binary checklist; fails chapters with <40% code lines. Already-shipped mgsr→sawc loop will re-roll those chapters automatically. | ~30 | Forces the LLM to actually USE the visible code, not write prose around it. | Ships #1 + #2 |
| 4 | **Diátaxis section template enforcement** — sawc_write prompt requires the 5-section Diátaxis structure. Reject sections that don't follow (Pydantic schema). | ~50 | Pedagogical structure becomes mechanical, not optional. | Ships #1 + #2 |
| 5 (Phase 2) | **`sawc_derive` node** — analogical prompting + MPSC for derived business-logic code. Enables the "AI generates new code adapted to your use case" goal. | ~150 | The full vision: LLM produces NEW code adapted from learned patterns. | Ships #1-4 |
| 6 (Phase 2) | **AST-normalized audit + derived/hallucinated classification** — replaces simple byte-match audit with similarity-tiered classification. | ~90 | Lets derived code coexist with verbatim without false-positive audit failures. | Ship #5 |

Phase 1 total (#1-4): ~430 LOC. Phase 2 (+#5-6): +240 LOC.

## 7. Free-tier compatibility — all 6 ships pass

| Component | Free-tier path |
|---|---|
| Visible Vault | Existing FGTS-VA rotator (NIM / Mistral / Gemini / etc.). LLM sees code in prompt — within free-tier context windows (Llama-4-maverick 1M, Mistral-Large-2 128K, qwen3.5-397b 128K). |
| Code inventory scoring | Pure Python heuristics (LOC, keyword match, AST tree-sitter for primitives) — no LLM. |
| Diátaxis enforcement | Pydantic schema + 1 sawc_write prompt change. |
| sawc_derive | Existing bandit-routed LLM (1 LLM call per derive marker × 3 samples for MPSC). |
| Audit similarity | `difflib.SequenceMatcher` + tree-sitter normalization — pure Python. |

**No paid APIs. No fine-tuning. No local LLM inference inside COELHO Cloud.** All proposed changes route LLM calls through the existing FGTS-VA bandit rotator per `project_local_vs_rotator_architecture`.

## 8. What's NOT shipping and why

| Technique | Why skip |
|---|---|
| **Constrained decoding via FSM (Copy-as-Decode, Outlines grammar)** | Requires inference-engine control (vLLM, SGLang, TensorRT). Free-tier rotators expose OpenAI-compatible REST — no logit access. |
| **Fine-tuning a copy-aware student model** | Violates free-tier-only constraint. Copy-as-Decode paper shows even with 131-385 oracle examples, 12-17% EM — bad ROI. |
| **Code-execution-based verification (sandboxed `exec`)** | User explicitly said derived code doesn't need to run. Adds infra + CVE surface. Tree-sitter + MPSC + API-parity check catches hallucinations without execution. |
| **Watermarking (SWaRL, CodeIP, MCGMark)** | Solves provenance of LLM-generated code, not code-fidelity-from-source. Wrong tool. |
| **Pure-extractive Verbatim RAG (KR Labs)** | Throws away generative commentary entirely. Too restrictive — we want the LLM to write prose AROUND code. |
| **AutoChecklist Deductive adaptive criteria** | Already ranked low in the earlier SOTA doc (defer until 50+ chapters); not specific to code-first. Will land later. |
| **5+ derived examples per pattern** | TOCE 2025 cognitive-load research shows 1 verbatim + 1 derived is the sweet spot for novice learners. More is overload. |
| **HippoRAG 2 / SurveyG hierarchical retrieval** | Already covered by Planner; doesn't address code visibility. |

## 9. Is the current pipeline heading in the correct direction?

**Yes, with one fixable architectural flaw.**

| Aspect | Correct direction? |
|---|---|
| 8-node Planner clustering 200-400 docs into chapters | ✅ Sound |
| Per-chapter Synth pipeline | ✅ Sound |
| Vault for byte-perfect code fidelity | ✅ Defensible (solves real Verbatim hallucination problem) |
| Sentinelization that hides code from LLM | ❌ Wrong abstraction for code-first goal — must evolve to visible envelopes |
| LLM-driven prose chapter writing | ✅ Sound, but needs density constraint to lean code-heavy |
| Free-tier bandit routing | ✅ Empirically working |

**The pipeline is the right machine pointed at the wrong target.** The Planner produces topically coherent chapter outlines; the Synth produces well-structured prose chapters. Both subsystems work. The missing piece is: **code is treated as a footnote, not as the primary content.** The vault made code structurally invisible to the LLM, and the SAWC prompt doesn't require it. Together they bias the output toward prose summaries.

Phase 1 (Visible Vault + inventory + density gate + Diátaxis template) flips that bias. The pipeline machinery itself stays.

## 10. Acceptance criteria (post-ship validation)

When Phase 1 lands, re-run the FastMCP study. The new README.md should show:

| Threshold | Target |
|---|---|
| Code fence count per chapter | ≥30 (currently 0) |
| Code line ratio (code / total lines) | ≥40% (currently 0%) |
| Word count per chapter | 2000-5000 (currently ~12,000 — too verbose) |
| Sections following Diátaxis template | ≥80% |
| Verbatim citations correctly substituted from vault | 100% (deterministic — by construction) |
| Hallucinated code claims | <5% per chapter |
| Audit passes after density gate | ≥80% (rest fail to mgsr loop → re-roll) |

If those thresholds pass on FastMCP and LangChain, the code-first re-architecture is validated. Phase 2 (sawc_derive) ships next.

## Sources

- [Verbatim Data Transcription Failures in LLM Code Generation (arXiv 2601.03640, Jan 2026)](https://arxiv.org/abs/2601.03640)
- [Copy-as-Decode: Grammar-Constrained Parallel Prefill (arXiv 2604.18170, Apr 2026)](https://arxiv.org/abs/2604.18170)
- [Frankentext: Stitching verbatim fragments (arXiv 2505.18128)](https://arxiv.org/abs/2505.18128)
- [Copy-Paste to Mitigate LLM Hallucinations (arXiv 2510.00508)](https://arxiv.org/abs/2510.00508)
- [When LLMs Meet API Documentation (arXiv 2503.15231)](https://arxiv.org/abs/2503.15231)
- [CogCanvas: Verbatim-Grounded Artifact Extraction (arXiv 2601.00821, Jan 2026)](https://arxiv.org/abs/2601.00821)
- [Deterministic Quoting (Yeung, healthcare LLMs)](https://mattyyeung.github.io/deterministic-quoting)
- [VerbatimRAG (KR Labs, ACL BioNLP 2025)](https://huggingface.co/blog/adaamko/verbatimrag)
- [Large Language Models as Analogical Reasoners (Yasunaga, ICLR 2024)](https://openreview.net/forum?id=AgDICX1h50)
- [Multi-Perspective Self-Consistency for code (arXiv 2309.17272)](https://arxiv.org/abs/2309.17272)
- [Worked examples + completion tasks (TOCE 2025, 10.1145/3732791)](https://dl.acm.org/doi/full/10.1145/3732791)
- [Pattern-conditioned synthesis (ICLR 2026, arXiv 2510.27246)](https://arxiv.org/abs/2510.27246)
- [Diátaxis Documentation Framework](https://diataxis.fr/)
- [Anthropic Claude Cookbooks (empirical code-first reference)](https://github.com/anthropics/claude-cookbooks)
- Current Synth code: `apps/fastapi/domains/dd/synth/`
- Prior Synth research: [`docs/KD-SYNTH-SOTA-2026-05-24.md`](./KD-SYNTH-SOTA-2026-05-24.md), [`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`](./SYNTH-ARCHITECTURE-SOTA-2026-05-18.md)
- Empirical evidence: `synth/fastmcp/ch-01-fastmcp-server-applications/README.md` (83 KB, 0 code fences, 0/0 code_refs resolved)
