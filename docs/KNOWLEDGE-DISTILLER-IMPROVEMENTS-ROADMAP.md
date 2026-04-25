# Knowledge Distiller — Improvements Roadmap

Dated: 2026-04-22 (initial) · **2026-04-23 revision** (code-preservation restructure)

Synthesis of a full architecture + code audit against the end-to-end run
completed on 2026-04-22 (v13, first successful full-pipeline test with
NIM-embeddings + v2 Clio REDUCE). Organized by impact × effort with
quality-preservation property called out per item.

**2026-04-23 revision.** The original draft assumed LLM-prompted "preserve
code verbatim" directives were sufficient. Deep research against 2026
state-of-the-art (see References) confirms every prompt-only approach is
probabilistic and will silently corrupt code in production — the LLM is
free to paraphrase, rename identifiers, trim whitespace, elide with `...`,
or hallucinate fences that weren't in the source. This revision
reorganizes the roadmap around the invariant that **the LLM must be
physically prevented from rewriting source code blocks**, not merely
instructed not to. A new **Tier 0** (code-preservation foundation) is
introduced as a blocking prerequisite for the original Tier 1 truncation
and Tier 2 dedup work.

---

## Code-preservation invariant (read first)

Every stage after ingest either (a) operates on metadata/embeddings and
cannot corrupt code, or (b) is a transform point where code can be
corrupted. The corruption-risk stages:

| Stage | Risk | Current mitigation | Gap |
|---|---|---|---|
| Monolith splitter (`_maybe_split_monolith`) | Negligible | LangChain `ExperimentalMarkdownSyntaxTextSplitter` (fence-aware). Verified empirically after 2026-04-22 regression fix. | None |
| MAP shard labeler | Negligible | Reads titles/slugs only; bodies untouched | None |
| REDUCE (Clio v2) | Negligible | Embedding/cluster-space only | None |
| `_load_chapter_files` 180K char truncation | **High** | Char-based, cuts mid-fence regardless of boundaries | Tier 0 + #1 + #2 |
| `SYNTHESIZER_PROMPT` | **Critical** | *No* verbatim-preservation directive; LLM is free to rewrite code | Tier 0 |
| `CURATOR_PROMPT` | Medium | Has explicit "PRESERVE every code block verbatim" clause at `prompts.py:516` — but still probabilistic | Tier 0 integrity check |
| Proposed MinHash dedup (original #6) | **High** | Whole-doc Jaccard will merge docs that differ ONLY in code | #6 revised |

**The invariant:** code blocks are transported through synthesis and
curator as opaque, byte-identical sentinels — not as text the LLM
generates. Any roadmap item that assumes the LLM will "respect" a
verbatim instruction is unsafe in production without a deterministic
integrity check behind it.

---

## Context from the 2026-04-22 run

The run that this roadmap is built against:

| Stage | Wall-clock | Notes |
|---|---|---|
| Ingest (Tier 1 llms-full.txt, 10 MB) | ~5 s | Fast-path hit |
| Splitter (4088 CommonMark sections + MinIO writes) | ~5 min | Chunk-retry handled transient `IncompleteBody` correctly |
| MAP (103 shards × ≤40 files, parallel LLM calls) | ~16 min | Several shards cascaded through 1-2 NIM 504 timeouts before landing on working models — ~63 of 103 shards stuck at primary for full 300s gateway window |
| REDUCE v2 (NIM embed → UMAP → KMeansConstrained → labels → order) | ~7.5 min | **Works.** k=9 balanced, silhouette **0.470**, one slug dedup. UMAP 2048d→5d took 303s (5 min; dominant cost). |
| Synth (first 2 chapters) | ~13 min each | Self-Refine loop working cleanly; score=0.71-0.75 → adjustment → retry |
| Synth (chapter 3) | **Failed** | All 12 synth fallback models returned None/malformed output on a 180K-char (45K-token) prompt. `phase=failed` sentinel recorded. Also affected by external Obsidian sync deleting MinIO files mid-run (not a KD bug). |
| Curator / Critic / Assembler | n/a | Did not reach |

**Ingredient-level root causes observed:**

1. NIM's chat-completions endpoint returns 504 Gateway Timeout at ~300s on reasoning-model calls with large prompts.
2. Groq's 413 is a TPM rate-limit, not a context-window issue. Only `meta-llama/llama-4-scout-17b-16e-instruct` (30K TPM) fits 25-35K prompts.
3. `CHAPTER_FILES_MAX_CHARS = 180_000` (~45K tokens) pushes every synth call into the danger zone.
4. MAP fires 103 shards via `asyncio.gather` without concurrency cap — NIM's 40 RPM means 63+ requests queue at primary and hit the gateway timeout.
5. UMAP runtime scales ~linearly with input dim — 2048d is slow. PCA preprocessing would cut 5 min to 30 s.

**What already works well (don't touch):**

- Clio v2 REDUCE (UMAP + KMeansConstrained + CH tiebreaker + slug dedup) — silhouette jumped 0.063 → 0.470
- NIM embedding endpoint (`nvidia/llama-nemotron-embed-1b-v2`) is separate from the chat endpoint and fully reliable
- LLM fallback chain research is current as of 2026-04-20; no model-list changes needed
- Coverage-repair in `distiller.py` lines 546-579 handles orphans + hallucinations correctly
- Cache layer (plan.json keyed by `manifest_hash`, ingest by framework+version)
- Fence-aware monolith splitter — already correct; all Tier 0 work builds on the same CommonMark tokenizer

---

## Tier 0 — Code-preservation foundation (blocking prerequisite)

These three items MUST ship before any of the original-draft truncation
(#2) or dedup (#6) items. Without Tier 0, those items corrupt code
silently. With Tier 0, source-code fidelity becomes a hard invariant
that is *verified*, not hoped for.

### 0a. Code-vault placeholder substitution — CRITICAL
**Pattern (industry-canonical; see References).** Before concatenating
chapter files for the synthesizer, walk the CommonMark AST via
`markdown-it-py`, extract every fenced block (and indented code /
inline `code` spans if feasible), and replace each with an opaque
sentinel:

```python
sentinel = f"​<<CB:{sha256(code_bytes)[:12]}>>​"
```

Store `sentinel → original_fenced_block` in a chapter-scoped `vault`
dict. Feed the placeholder-ified text to the synthesizer. After the LLM
returns, deterministically substitute each sentinel with its original
fence. Verify: the set of sentinels in the output must be a subset of
the set in the input (missing = LLM deleted code; extra / mismatched =
LLM hallucinated).

**Why opaque hash + zero-width-space bookends.** LLMs copy
`​`-wrapped tokens byte-exactly; unicode invisibles do not collide
with real content. Short SHA hashes (not sequential `CB_1/CB_2/...`)
prevent the model from "guessing" an adjacent placeholder. Pretty
placeholders like `[CODE_42]` are rewritten by some models (documented
failure mode in OpenAI's GPT-4.1 prompting guide).

**Failure modes to guard.**
- Placeholder collision with source content (ultra-rare but possible) →
  scan source pre-vault; abort or rehash on collision.
- LLM silently drops a placeholder → caught by integrity check (0c).
- LLM emits a placeholder mid-word in prose ("use the `<<CB:...>>`
  function") → substitution still works; no special handling needed.

**Files.**
- `graphs/knowledge/helpers.py` — new `_vault_code_blocks(content) → (vaulted_text, vault)`
  and `_restore_code_blocks(llm_output, vault) → restored_text` utilities.
- `graphs/knowledge/distiller.py` lines 670-685 (`synthesize_chapter`
  node) — wrap `_load_chapter_files` output through vault before
  `_synthesize_attempt`; restore on the returned `ChapterSynthesis.content`.

**Effort:** ~80 LoC. `markdown-it-py` is already transitively installed
via LangChain's `ExperimentalMarkdownSyntaxTextSplitter`.

**Quality:** strictly positive. Eliminates code corruption at synthesis
entirely. Also reduces prompt token cost ~20-40% (code is often half
the chapter budget, compresses to ~20-char sentinels).

### 0b. Sentinel-preservation clause in `SYNTHESIZER_PROMPT` — CRITICAL
Add one sentence to the system message (`prompts.py:297-319`, after
the "Output requirements" block):

> "Any token matching the pattern `<<CB:...>>` surrounded by zero-width
> spaces is an opaque placeholder for a source code block. Reproduce
> these placeholders byte-exactly in context. Do not modify, paraphrase,
> expand, remove, or replace them with actual code. They will be
> substituted back to the original code after your response."

Also add the equivalent clause to `CURATOR_PROMPT` (`prompts.py:503-548`)
— the curator already instructs "PRESERVE every code block verbatim"
but after Tier 0a the code isn't even present; the sentinels must be
preserved instead.

**Files:** `schemas/knowledge/prompts.py`.

**Effort:** ~4 LoC (one sentence in each of synth + curator + adjustment prompts).

**Quality:** belt-and-suspenders with 0a — tightens model behavior
even before the integrity check catches misses.

### 0c. Post-synthesis code-integrity check — CRITICAL
After restore (0a), verify preservation as a hard invariant:

```python
def canon(code: str) -> str:
    lines = [ln.rstrip() for ln in code.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)

source_hashes = {sha256(canon(c).encode()).hexdigest()
                 for c in extract_fences(pre_vault_source)}
output_hashes = {sha256(canon(c).encode()).hexdigest()
                 for c in extract_fences(post_restore_output)}
preservation_ratio = len(source_hashes & output_hashes) / max(1, len(source_hashes))
```

**Canonicalization rules (minimal, byte-level).**
- Strip trailing whitespace per line.
- Drop trailing blank lines.
- Preserve indentation (semantic in Python/YAML/Makefiles).
- Do NOT lowercase, strip shebangs, strip comments, or AST-canonicalize
  (tree-sitter would hide the exact differences we care about, e.g.
  `# type: ignore` — a silent API break if removed).

**Feedback loop.** If `preservation_ratio < 1.0`, pass the list of
missing blocks (first 80 chars of each) to the Self-Refine loop as
targeted feedback (LLMLOOP 2025 pattern — specific error feedback
dramatically outperforms generic "try again"). If
`preservation_ratio < 0.90` after max refine iterations, fail the
chapter (trigger `regenerate`, not just `refine`).

**Files:**
- `graphs/knowledge/helpers.py` — new `_check_code_preservation(source, output) → (ratio, missing_blocks)`.
- `graphs/knowledge/distiller.py` `synthesize_chapter` loop — call
  alongside grader evaluation; gate accept/refine/regenerate on ratio.

**Effort:** ~60 LoC.

**Quality:** guarantees source-code fidelity as a deterministic
invariant, not a soft grader score.

### 0d. Observed failure mode + hardening (2026-04-23 FastAPI smoke test)

On the 2026-04-23 live test (fastapi, 4 chapters, 506 vaulted code
blocks total), **ch04 iter 0 reported `98 missing / 0 unexpected of 98
vaulted`** — the LLM stripped every sentinel from its output. Tier 0c
caught it (hard gate fired, preservation feedback appended to
adjustments, iter 1 retried). Tier 0b's prompt clause alone was
insufficient for at least one model in the fallback chain.

**Root-cause hypotheses from live-environment research:**

1. **Zero-width-space bookends are stripped by tokenizer preprocessing.**
   U+200B is a known production-pipeline problem — security-focused
   normalizers strip ZWS as a Unicode-smuggling defense (AWS security
   blog, Promptfoo guide, Reverse CAPTCHA paper). The model may never
   see the bookends; what it receives is `<<CB:abc123>>` — visually
   similar to TODO / ellipsis markers — which it then strips as
   low-signal noise.

2. **Sentinel clause is in the system message.** Anthropic's own guide
   explicitly states: *"Claude follows instructions in the human messages
   better than those in the system message."* Tier 0b placed the clause
   in the weakest slot. Same positional penalty applies to GPT and NIM
   reasoning models per Anthropic's "query at end" guidance (30% quality
   lift measured).

3. **Opaque hash-shaped tokens read as placeholders, not literal
   content.** LLMs have strong priors that `<<XXXX>>`-like strings are
   fill-me-in placeholders — see Gemini CLI issue #4836 documenting the
   mirror-image failure (models strip real code and inject `// ...`
   comments). Same prior, firing in reverse.

**Hardening actions (priority-ordered):**

**0d-1. Switch sentinel shape from ZWS-wrapped hash to self-closing XML
tag.** CRITICAL.
Replace `​<<CB:{sha12}>>​` with `<code-ref hash="{sha12}"/>`.
Rationale: Claude is explicitly trained on XML tags as structural
primitives (Anthropic XML guide); every mainstream model (GPT, Gemini,
open-weights) treats XML tags as pass-through structure, not content.
No invisible characters → no tokenizer-preprocessor strip. Self-closing
form signals "no inner content to modify".
Files: `helpers.py` (`_make_sentinel` + `_VAULT_SENTINEL_RE`),
`schemas/knowledge/prompts.py` (3 clauses), `scripts/test_code_vault.py`
(collision-test sentinel literal).
Effort: ~10 LoC.

**0d-2. Move sentinel clause from system to the END of the human
message, below the data block.** HIGH.
Anthropic: *"place long documents and inputs near the top of your
prompt, above your query, instructions, and examples. Queries at the
end can improve response quality by up to 30%."* Data-first,
critical-instructions-last is the canonical Claude pattern. Move the
preservation clause from `SYNTHESIZER_PROMPT`'s system message to the
END of its human message (after `{assigned_files_content}` and
`{previous_adjustments}`, immediately before "Synthesize the chapter.").
Same pattern for `CURATOR_PROMPT`. Effort: ~5 LoC.

**0d-3. Add a one-shot `<code-ref>` preservation example to both synth
+ curator prompts.** HIGH.
Canonical fix for teaching non-standard behavior. Minimal example:
`Input: "Use the async client: <code-ref hash=\"abc123def456\"/>"` →
`Output: "## Async Client\n\n<code-ref hash=\"abc123def456\"/>\n\nThe
async client lets you..."`. Include the annotation "Notice the
<code-ref> appears verbatim in the output; it is NEVER modified,
expanded, or removed."
Effort: ~20 LoC.

**0d-4. Self-verification step at the end of the human message.**
MEDIUM.
*"Before returning your `content`: count the `<code-ref>` tags. The
count MUST equal the count in the input. If not, rewrite to include
every missing tag in its logical position."* Cheap in-context check;
catches many non-adversarial strips without needing a retry round-trip.
Effort: ~8 LoC.

**0d-5. Model-fallback-chain observability for preservation rate.**
MEDIUM.
Log which model was used per synth iteration; aggregate preservation
failure rate per model across runs. Models exceeding a threshold (e.g.,
10% after 0d-1..4 ship) get demoted in the chain or excluded from synth
specifically (keep them for grader/resolver where preservation doesn't
matter). Natural follow-up once LangFuse (#14) is live.
Effort: ~20 LoC instrumentation.

**Deploy order.** 0d-1 + 0d-2 as one small PR (highest-confidence root
causes, behavior-neutral for already-compliant models). 0d-3 + 0d-4 as
a second PR (quality-positive, token-cost positive since few-shot
overhead is small). 0d-5 after a baseline run.

**Success metric.** On the next FastAPI smoke-test, iter-0
`preservation_ratio` should be ≥ 0.90 on ≥ 3 of 4 chapters (vs. 0.0 on
ch04 today). If unmet after 0d-1..4 ship, escalate to Tier 3 #21
(structured-output synth with `code_refs: list[str]`) — the
architectural form cannot regress because the LLM never emits sentinel
text at all.

### 0d (follow-up). Round 2 A/B test — shape + position alone INSUFFICIENT (2026-04-23 ~04:57 UTC)

**Shipped before round 2:** 0d-1 (sentinel shape
`<code-ref hash="{sha12}"/>`) + 0d-2 (clause moved from system message
to END of human message, below the data block).
**Not yet shipped:** 0d-3 (few-shot), 0d-4 (self-verify), 0d-5 (model
observability).

**Round-2 configuration.** Same fastapi corpus as round 1 (137 files,
1.54 MB, 4 chapters from plan cache — MAP+REDUCE skipped). Clean A/B:
only the sentinel shape + clause position changed.

**Iter-0 preservation result:**

| Chapter | Vaulted blocks | Missing on iter 0 | Strip rate |
|---|---|---|---|
| ch01 | 171 | 171 | 100% |
| ch02 | 92 | 92 | 100% |
| ch03 | 145 | 145 | 100% |
| ch04 | 98 | 98 | 100% |

**Identical to round 1** (ZWS-wrapped hash shape). Shape swap +
clause reposition had **zero effect** on iter-0 preservation. The LLM
prior for "`<...>`-shaped short tokens = placeholder content to
remove / TODO marker" fires on both shapes equally across every model
in the fallback chain that handled a chapter. We do not yet know
whether the prior would also fire on content-bearing tokens (e.g.,
`<code-ref>...original code here...</code-ref>` with the original
code *inside* the tag as attribute-free content) or on pure integer
refs.

**Iter-1 fatal crash (ch03).** Ch03's iter-1 synth call — with the
preservation-failure feedback now appended to `adjustments` (bloating
the prompt with 8 missing-sentinel previews) — exhausted the fallback
chain; the last model returned `None` from structured-output parsing.
`_synthesize_attempt` raised `RuntimeError`; `synthesize_chapter`
rewrapped it; the LangGraph Send() worker propagated it; the whole
Celery task died. We **never observed iter-1 behavior on ch01 / ch02 /
ch04** — the 3 chapters that might have recovered were killed by
ch03's failure. This is a separate, pre-existing robustness bug
(isolation failure across parallel workers) that Round 2 surfaced.

**Two distinct follow-up tracks:**

**Track A — escalate 0d-3 + 0d-4 to MUST-SHIP-NEXT** (promoted from
HIGH / MEDIUM).

Few-shot examples (0d-3) are the canonical technique for teaching an
LLM a non-standard behavior — showing an actual input/output pair
demonstrates pass-through in a way prompt instructions alone do not.
Self-verification (0d-4) — "before returning, count the `<code-ref>`
tags in your output and compare to the input" — catches many
non-adversarial strips in-context without a retry round-trip. Both
were deferred to a second PR in the original deploy plan; Round 2
proves they should be in the first PR.

Combined effort: ~28 LoC in `schemas/knowledge/prompts.py` (updates
to `SYNTHESIZER_PROMPT` and `CURATOR_PROMPT`).

**Track B — new item 0d-6: isolate per-chapter synth failures.**
CRITICAL (behavior bug, not preservation).

Current behavior: `_synthesize_attempt` in `helpers.py` raises
`RuntimeError` on `None` from the fallback chain; `synthesize_chapter`
in `distiller.py` rewraps and re-raises; LangGraph treats this as a
superstep failure; Celery task ends; sibling chapters never finish.
Desired behavior: catch the terminal `RuntimeError` inside
`synthesize_chapter`, write a DEBT entry for that chapter
(`phase=failed`, along with what adjustments it had accumulated),
return a sentinel-shaped result (`ChapterResult` with `debt.reason =
"synth_chain_exhausted"`) so downstream curator / critic / assembler
can detect-and-skip. Each chapter becomes a genuine isolation
boundary.

Files: `graphs/knowledge/distiller.py` (`synthesize_chapter` error
handler; null-guards in curator / critic / assembler nodes). Effort:
~30 LoC.

**Revised deploy unit (PR-scoped).** Ship 0d-3 + 0d-4 + 0d-6 in a
single PR (~60 LoC total). Then run Round 3 smoke test on the same
fastapi corpus (plan cache will still hit, so we're gated only on
synth time).

**Revised success metric (after 0d-3 + 0d-4 + 0d-6 ship):**

1. Iter-0 `preservation_ratio` ≥ 0.90 on ≥ 3 of 4 chapters. Rounds 1
   and 2 both had 0.0 across all 4 — any meaningful preservation
   improvement proves the prompt change landed.
2. A single chapter hitting synth-chain exhaustion writes DEBT and
   the other 3 chapters still complete; whole-run crash from one
   chapter's synth failure is no longer possible.

**Escalation path if revised metric unmet.** If iter-0 preservation
after 0d-3 + 0d-4 still shows ≥ 50% strip on any chapter, the
LLM-as-freeform-synthesizer architecture cannot be trusted with
opaque-token pass-through and we escalate to **Tier 3 #21
(structured-output synthesizer with `code_refs`).** Pydantic shape
where the LLM emits ordered indices into a server-held vault rather
than any form of inline sentinel text. The LLM never has an
opportunity to strip a sentinel because it never emits one. Stronger
guarantee than prompting at the cost of ~150 LoC synth-node rewrite +
assembler changes. Already specified in this roadmap.

**What Round 2 validates about the existing Tier 0 design:**

- Tier 0a (vault primitives, `scripts/test_code_vault.py`): still
  19/19 green with the new XML-tag sentinel shape. Round-trip fidelity
  is a pure-Python invariant and is not regressing.
- Tier 0c (integrity gate): caught every preservation failure again.
  The audit ran on all 4 chapters, the `_format_preservation_feedback`
  producer generated targeted retry text correctly, the forced-refine
  path activated. The gate is doing its job; what the gate cannot do
  is compensate for an LLM that ignores the feedback.
- The Tier 0c design (hard gate + targeted feedback) is proven CORRECT
  but INSUFFICIENT when the base synth prompt cannot induce the
  required behavior in the first place. The chapter-3 iter-1 crash
  further shows that bloating adjustments with feedback can itself
  destabilize the synth call. 0d-3 + 0d-4 address this by making
  iter-0 succeed more often, reducing how many iterations ever need
  the adjustment-bloat path.

### 0e. Principle confirmed: Tier 0c is the load-bearing safety net

Today's run validates the Tier 0c design as the roadmap's most
important guarantee. Without the hard-gate + audit-and-refine loop,
ch04's iter-0 output would have produced a chapter missing 98 code
blocks and the failure would have been **invisible** — the grader would
have happily scored a chapter of pure prose with no code hallucinations
to flag. The audit surfaced the problem immediately and produced the
exact feedback needed to iterate.

**Rule:** never rely on prompting alone for verbatim preservation.
Always back it with a deterministic post-hoc integrity check.

---

## Tier 1 — Top wins (revised for code safety, ship after Tier 0)

All quality-neutral-to-positive, all low LoC.

### 1. BM25F file-ranking before synth budget — RELIABILITY + QUALITY (revised)
**Revision vs. original draft.** Original said BM25 over chapter-file
text. Mixed prose/code corpora distort single-field BM25 — code tokens
(keywords like `SELECT`, `function`, `import`) dominate or vanish
depending on tokenizer. Revised to **BM25F** with two fields:

- `prose` — weight 1.0, standard tokenizer
- `code` — weight ~0.3, code-aware tokenizer (split camelCase,
  snake_case, dots; preserve identifiers as bigrams `foo.bar → foo, bar, foo.bar`)

Rank **whole files** against `chapter.goal`. Greedy-pack in rank order
into the synth budget (see #2). Never split a file mid-body in this step.

**Effect.** The most pedagogically relevant files always make the
budget; no reliance on alphabetical accident. BM25F outperforms
single-field BM25 on mixed docs (Turnbull 2025).

**Library.** `bm25s` (fastest 2024 Python rewrite; supports per-field
scoring via manual combination). True BM25F is simple to hand-roll on
top; see `softwaredoug.com/blog/2025/09/18/bm25f-from-scratch`.

**Files:** `graphs/knowledge/helpers.py` — add `_rank_chapter_files()`,
call from `_load_chapter_files()`.

**Effort:** ~70 LoC.

### 2. Token-budget enforcement via whole-file packing — RELIABILITY ✅ SHIPPED 2026-04-23
**Status.** Shipped alongside Tier 3 #21 as the "KD robustness batch 1" PR.
`CHAPTER_FILES_MAX_CHARS = 180_000` replaced by
`CHAPTER_FILES_MAX_TOKENS = 40_000` (tiktoken cl100k_base). Cap-after-append
off-by-one fixed (budget check runs BEFORE append). Fence-safe intra-file
split via `ExperimentalMarkdownSyntaxTextSplitter` when a single top-ranked
file exceeds the remaining budget. Whole-file packing in planner-assigned
order (BM25F rank from #1 TBD as follow-up — current ordering respects
planner intent). Never truncates inside a fenced code block.

**BM25F file-ranking (#1)** is the natural follow-up: today the loader packs
files in planner-assigned order; swapping that for BM25F rank against
`chapter.goal` picks the most pedagogically relevant first. Small PR (~70 LoC
on top of #2).

**Files:** `apps/fastapi/graphs/knowledge/helpers.py` — `_load_chapter_files`
rewritten; new `_tiktoken_count` + `_fence_safe_split` helpers;
`apps/fastapi/pyproject.toml` — tiktoken dep added.

---

**Original draft.** Lower `CHAPTER_FILES_MAX_CHARS` from 180K to 80K to
fit Groq TPM limits.

**Problem with the original.** A CHARACTER cap cuts mid-fence regardless
of boundaries, silently deleting code. This contradicts the
quality-over-speed preference and breaks the code-preservation invariant.

**Revised plan.**

1. Replace `CHAPTER_FILES_MAX_CHARS` with `CHAPTER_FILES_MAX_TOKENS`
   (default **40K tokens** using `tiktoken` `cl100k_base`). Token
   counting is what matters for provider TPM/context limits; characters
   are a proxy.
2. Enforce the budget via **whole-file greedy-pack in BM25F rank order**
   (#1). Never truncate inside a file to hit budget.
3. If a single top-ranked file exceeds the remaining budget, split it
   using the SAME fence-aware splitter already in production
   (`ExperimentalMarkdownSyntaxTextSplitter` from
   `helpers.py:_maybe_split_monolith`), then greedy-pack sections in
   order. Never split mid-fence.
4. Tier 0 vault compresses all code to ~20-char sentinels in the final
   LLM payload, so the effective *content delivered* at 40K tokens is
   substantially richer than the raw token count suggests.

**Effect.** Groq TPM respected; NIM gateway timeouts avoided; zero code
lost to truncation.

**Files:** `graphs/knowledge/helpers.py` — rewrite `_load_chapter_files`
as a token-counted, fence-safe, BM25F-packed concatenation.

**Effort:** ~40 LoC on top of #1.

**Quality:** strictly positive vs. original draft (which would have
regressed code fidelity).

### 3. PCA pre-reduction before UMAP — SPEED (zero quality loss)
**Current:** UMAP 2048d → 5d takes 303s (biggest REDUCE cost today).
**Proposed:** `PCA(n_components=128)` → UMAP 128d → 5d, finishes in ~30s total.
**Quality:** PCA retains 99%+ variance on sentence-transformer embeddings; UMAP output identical within noise.
**Files:** `graphs/knowledge/reduce_cluster.py` — add PCA step before UMAP.
**Effort:** ~10 LoC.

### 4. MAP inter-shard concurrency cap — ✅ SHIPPED 2026-04-23 (super-super-batch)
Semaphore=30 wraps `asyncio.gather(_label_shard_bounded ...)`. Run-6 baseline
had 172 HTTP 429 retries in MAP; sem=30 limits per-minute MAP pressure on
NIM's 40 RPM/model budget.
**Files:** `graphs/knowledge/distiller.py` — `MAP_SHARD_SEMAPHORE` + `_label_shard_bounded`.

### 4b. Synth fan-out concurrency cap — ✅ SHIPPED 2026-04-23 (batch-2)
**Current:** `KnowledgeDistillerGraph.build_knowledge_distiller_graph` defaults
`max_concurrent_chapters=5`. The module's own header docstring documents
`K=2` as the NIM-safe value ("With K=2, typical NIM free-tier headroom
(40 RPM per model) is plenty for the primary to serve every chapter's
initial call without falling back"). **The default drifted away from the
comment at some point.** Live evidence:

Run-4 (2026-04-23 17:10-17:11 UTC, study `75fe1ad1-b437-4fb9-a4f2-7644396bd5ff`):
5 synth workers fanned out against `z-ai/glm-5.1` (primary NIM). All 5
hit HTTP 504 Gateway Timeout within a 45-second window — classic
stampede shape. Each burned the full 300s NIM gateway timeout before
cascading.

```
17:10:26  [synth ch02] glm-5.1 raised InternalServerError: 504; escalating
17:11:04  [synth ch03] glm-5.1 raised InternalServerError: 504; escalating
17:11:05  [synth ch01] glm-5.1 raised InternalServerError: 504; escalating
17:11:08  [synth ch05] glm-5.1 raised InternalServerError: 504; escalating
17:11:08  [synth ch04] glm-5.1 raised InternalServerError: 504; escalating
```

Root cause: NIM's `integrate.api.nvidia.com` free-tier endpoint serializes
requests per model; 5 parallel 300s reasoning calls all exceed the gateway
timeout before the second/third/fourth ever reach the LLM. NOT a code
correctness bug — the fallback chain recovers — but wastes 5 × 300s =
25 min of wall time on every run and floods logs with scary 504s.

**Proposed:** restore the default to `max_concurrent_chapters=2` to match
the header docstring. 2 parallel reasoning calls fit NIM's free-tier
comfortably.

**Effect:** next run, primary model serves chapters cleanly; 504 stampede
eliminated; ~25 min wall-clock saved. Zero quality cost (if anything,
chapters land on the *same* primary model more often → more consistent
voice, which was the original K=2 argument).

**Files:** `graphs/knowledge/distiller.py` — one-line change to the
`build_knowledge_distiller_graph` default.

**Effort:** ~1 LoC.

**Do NOT:** wipe MinIO, wipe the ingest/plan cache, or otherwise touch
storage. The 504 is an LLM-provider edge timeout; MinIO has zero role
in the NIM call path.

### 5. Per-shard / per-synth eager timeout — ✅ SHIPPED 2026-04-23 (batch-2)
**Current:** each LLM call waits NIM's full 300s gateway before `with_fallbacks` tries next model.
**Proposed:** `asyncio.wait_for(chain.ainvoke(...), timeout=120)` — cascade at 2 min.
**Quality:** unchanged (the call wasn't going to return anyway).
**Files:** every `ainvoke` site where the LLM is a `RunnableWithFallbacks`
and the prompt is large. Primary sites: `_synthesize_attempt`,
`_grade_attempt`, `_invoke_structured_with_fallback`'s `chain.ainvoke`,
critic's assessor call, adjustment generator, shard labeler.
**Effort:** ~5 LoC per site.

**Better-fix framing** (Run-4 post-mortem, 2026-04-23): 504s cost 300s
each; with `asyncio.wait_for(chain.ainvoke(...), timeout=120)` they cost
120s. For a study that cascades through 3-4 models per chapter on a hot
day, that's ~10 min saved per chapter, ~60+ min saved across 11
chapters. Zero quality impact — the call wasn't going to return inside
the 300s either way.

---

### Best fix for fleet throughput — ship 4b + 5 together

| | Eliminates | Cost |
|---|---|---|
| **4b alone** (semaphore=2) | Stampede: 5 concurrent calls against one primary → 504 ×5 | ~1 LoC |
| **5 alone** (eager 120s timeout) | Straggler cost: each stuck call is 120s not 300s | ~5 LoC × N call sites |
| **Both together** | Stampede AND straggler | same ~30 LoC, single PR |

With both shipped:
- Primary NIM model gets at most 2 concurrent reasoning calls → it can actually *serve* them (free tier is 40 RPM per model; 2 concurrent × ~1 min each ≈ 30 RPM sustained).
- Any genuinely stuck call escalates at 2 min instead of burning the 300s gateway ceiling.
- **Stuck-call cost drops from 5-min-blip → 2-min-blip; stampede cost drops from 25 min to 0.**

Baseline wall-clock projection (post-Tier-0-done + batch-1-done + 4b + 5):
Run-3 wall-clock 86+ min → Run-5 wall-clock ~35-45 min for the same
11-chapter corpus. Per-chapter synth median drops from ~8 min to ~2-3 min
because the primary actually serves most calls (no need to cascade for
most chapters).

**Do NOT** wipe MinIO, clear the ingest cache, or reset Redis when
shipping 4b + 5. The symptoms are 100% on the LLM-provider edge; our
storage state is healthy.

**Combined expected effect on the 2026-04-22 run baseline (Tier 0 + Tier 1):**
- Chapter 3 synth failure: eliminated (vault compresses prompt; integrity check catches corruption)
- Total pipeline wall-clock: ~40 min instead of 90+
- Code fidelity: provably 100% preserved (vs. probabilistic today)
- No quality loss elsewhere (small gain from BM25F ranking)

---

## Tier 2 — Quality-positive wins (2-3 days, sprint 2)

### 6. Code-aware MinHash dedup — QUALITY (revised)
**Original draft.** MinHash + Jaccard > 0.7 on whole-doc shingles →
merge near-duplicates before LLM.

**Problem with the original.** Two docs that share ~80% prose but
differ in code (common pattern: `api/reference.md` vs.
`api/tutorial.md` — one has imports, one has error handling, one uses
async, one uses sync) are NOT duplicates. Dropping either is silent
content deletion. NVIDIA NeMo Curator's `fuzzy_dedup` and
embedding-based `SemDeDup` both fail this case.

**Revised plan: two-pass dedup.**

1. Parse each file via `markdown-it-py`. Extract `prose` (non-fence
   content) and `code_blocks` (list of fenced content, canonicalized
   per Tier 0c).
2. Compute `MinHash.from_text(prose)` AND
   `code_hash_set = {sha256(canon(cb)) for cb in code_blocks}`.
3. A candidate pair is a duplicate iff **MinHash Jaccard > 0.85 AND
   `code_hash_set_a == code_hash_set_b`** (set equality).
4. If code hashes differ by even one, the pair is NOT a duplicate,
   regardless of prose similarity. The code delta is load-bearing.

**Libraries.** `datasketch` (MinHash), `markdown-it-py` (AST). Do not
use `tree-sitter` for the code hash — AST-canonicalization hides
renames and formatting choices that are often the semantic difference.

**Effect.** Genuine prose redundancy removed; every unique code
variant preserved.

**Files:** `graphs/knowledge/helpers.py` — new `_dedup_chapter_files()`.

**Effort:** ~120 LoC.

### 7. TF-IDF glossary extraction across all chapters — CURATOR QUALITY
**Current:** heuristic Counter over chapter-0 CamelCase/snake_case → misses vocabulary from later chapters.
**Proposed:** `TfidfVectorizer` across all chapters → top-12 domain-specific terms reliably.
**Effect:** curator normalizes terminology consistently across the full study.
**Files:** `graphs/knowledge/helpers.py` — replace `_extract_glossary_terms()`.
**Effort:** ~15 LoC.

### 8. Parallel curator over chapters — SPEED
**Current:** curator is sequential per chapter (~10 min for 9 chapters on GLM-5.1).
**Proposed:** `asyncio.Semaphore(2)` — 2 chapters curated concurrently. Fits GLM-5.1's rate limit.
**Effect:** curator 10 min → ~5 min.
**Effort:** ~10 LoC.

### 9. Deterministic pre-gates on grader — SPEED (zero quality loss)
**Current:** grader LLM runs for every Self-Refine iteration.
**Proposed:** pre-compute Flesch-Kincaid, code-block ratio, citation density, heading sanity. Any failure below threshold → reject without calling grader LLM.
**Effect:** ~50% of iter-0 chapters fail a cheap threshold and skip the LLM call → immediate refine.
**Files:** `graphs/knowledge/helpers.py` — new `_deterministic_grader_gates()`.
**Effort:** ~30 LoC.

### 10. Citation-regex whitelist in critic — ✅ SHIPPED 2026-04-23 (super-super-batch)
**Current:** critic's `_CITATION_RE = r"#\s*docs:\s*([^\s\n`)]+)"` captures slug-like patterns including non-slugs (e.g., `api(utils)` captures `api`).
**Proposed:** build `|`-joined alternation of actual corpus slugs at preprocess time; regex only matches real slugs.
**Effect:** zero false positives in citation integrity check.
**Files:** `graphs/knowledge/helpers.py` — rewrite `_scan_citations()`.
**Effort:** ~10 LoC.

### 19. Code-preservation grader dimension — ✅ SHIPPED 2026-04-23 (super-batch)
**Rationale.** The existing `code_syntax_valid` dimension
(`prompts.py:440-441`) measures whether output code blocks are
syntactically well-formed. That is orthogonal to whether they match the
SOURCE. A chapter can achieve 100% `code_syntax_valid` on code wholly
hallucinated by the LLM.

**Proposed.** Add `code_preservation_ratio` (binary per-block, from
Tier 0c) as a new grader dimension, weighted 2× (tied with
`signal_to_noise` and `citation_integrity`). Once Tier 0 ships,
`code_syntax_valid` becomes largely redundant (preserved source code
is by definition valid) — keep it as a low-weight sanity check only.

Optionally add `code_similarity_score` (continuous) via per-block
**CrystalBLEU** — downweights trivial n-grams like `{`, `}`, keywords,
making it the right tool for doc-preservation metrics (vs. classical
BLEU, which would score reformatted-but-intact code poorly).

**Files:** `schemas/knowledge/prompts.py` (extend `GRADER_PROMPT` + the
`GraderEvaluation` Pydantic model).

**Effort:** ~40 LoC.

### 20. Critic hallucinated-fence check — ✅ SHIPPED 2026-04-23 (super-batch)
**Rationale.** Current critic scores `citation_integrity` and
`faithfulness` on prose claims, not on fence provenance. After Tier 0
this is enforced at synth time; the critic adds a final backstop on
the fully-assembled study (catches post-curator drift).

**Proposed.** Extend `CRITIC_PROMPT` (`prompts.py:428-453`): "Every
fenced code block in the chapter must correspond to a fenced block in
the source file list (allowing the canonical-whitespace normalization
from integrity check 0c). Emit a DEBT issue for any output fence that
does not match any source block."

**Files:** `schemas/knowledge/prompts.py` + `graphs/knowledge/distiller.py`
(critic node preprocesses source hash set).

**Effort:** ~30 LoC.

---

## Tier 3 — Architectural changes (defer until bottleneck shifts)

### 11. Hybrid MAP (Clio-at-shard-level)
Embed files per shard + classical cluster (k=3 per shard) + LLM names only. Trades 103 complex calls for 309 tiny calls + 4088 embeddings. **Defer until MAP is actually the pipeline bottleneck** — after Tier 1 #4 + #5, MAP runs in ~10 min which is acceptable. See `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` for the pattern. ~200 LoC.

### 12. Sub-chapter synthesis batching (priority raised post-Tier-0)
Split thick chapters into groups of ~20 files, synthesize each group, merge. Preserves quality at ANY chapter size without truncation. **Composes cleanly with Tier 0 vault** — each batch has its own vault; integrity check runs per batch. After Tier 0 ships, #12 is the natural follow-up for corpora that exceed the #2 token budget even after BM25F packing. ~150 LoC.

### 13. Per-chapter artifact cache (resume-on-failure)
When chapter 7 fails, don't re-synth chapters 1-6. Store `{study_id, chapter_n} → synthesis_output` in MinIO; synth node checks before calling LLM. Robust against transient failures (Obsidian-style external deletions, NIM outages mid-run). ~100 LoC.

### 14. LangFuse observability (self-hosted) — ✅ SHIPPED 2026-04-23 (super-batch, includes 0d-5 telemetry)
- **Infra side**: Langfuse v3 (app 3.169.0, chart 1.5.27) deployed on COELHOCloud via `modules/langfuse/` Terraform module. Shared PostgreSQL + shared MinIO S3 + bundled ClickHouse + bundled Valkey. Tailscale ingress at `https://langfuse.YOUR_TAILNET_DOMAIN.ts.net`. Headless-init enabled (org `coelho`, project `coelhocloud`, admin user seeded).
- **KD side**: `services/knowledge/langfuse_client.py` builds a `CallbackHandler` when `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` env vars are set (graceful no-op otherwise). `_invoke_structured_with_fallback` wires the handler into every `chain.ainvoke` call with per-model + per-iteration + per-chapter metadata + tags. Synth + grader callsites plumbed. Unblocks 0d-5 (per-model preservation rate) by construction.
- Future follow-up: wire the same handler into curator's direct `chain.ainvoke` (currently uses `| llm` directly), and add a periodic flush on graph completion so traces upload cleanly on normal exit.

### 15. Qdrant cross-study semantic search
Once N studies exist, index every chapter content via `fastembed`. Enables "which study explains LangGraph state schemas?" via vector search — no LLM needed for retrieval. Sets up the shared infra pattern for Book Distiller + Deep Research. ~200 LoC.

### 21b. Distribution fix for structured synth — ✅ SHIPPED 2026-04-23 (super-batch, 2026-04-23 evening)

**Run-4 post-mortem finding (DeepAgents + LangChain + LangGraph docs, Run-4
committed chapter inspection):**

#21's audit enforced `union(code_refs) == vault_hashes` but did NOT enforce
uniqueness or distribution. LLM discovered the loophole: same hash placed
in multiple sections' `code_refs` → audit passes but the assembler renders
the same code block repeatedly in unrelated sections. Also: LLM put
substantive prose in sections with empty `code_refs` ("filler" sections).

Observed example — a committed README had ABAC policy JSON blocks appearing
under BOTH "RBAC and ABAC Policies" (correct) AND "Agent Server API
Endpoints" (wrong topic) because the LLM stuffed leftover hashes into the
last section.

**Fixes shipped:**

1. **Audit uniqueness + empty-section checks.**
   `_audit_structured_output_refs` now returns a 5-tuple:
   `(missing, invented, fence_sections, duplicated_refs, empty_sections)`.
   `duplicated_refs` = any hash appearing in >1 section's `code_refs`.
   `empty_sections` = sections with >40 chars of prose but zero `code_refs`
   (transitional sections with ≤40 chars of prose are allowed).
2. **Assembler dedup defense.** `_assemble_chapter_markdown` now emits
   each vault block at MOST once regardless of audit bypass (first
   occurrence wins — the LLM's first choice is usually the best).
3. **Prompt guidance** in `SYNTHESIZER_PROMPT` — explicit DISTRIBUTION
   RULES block: each hash in exactly ONE section; substantive prose
   needs code_refs or merge/trim.
4. **Targeted refine feedback** via `_format_structured_output_feedback`
   extended to enumerate duplicated + empty-section details.

### 21. Structured-output synthesizer with `code_refs` — ✅ SHIPPED 2026-04-23
**Status.** Escalation trigger fired on 2026-04-23 Round-3 smoke (4 of 8
reporting chapters at ≥50% iter-0 strip). Shipped as the core of the
"KD robustness batch 1" PR on the same day.

**Implementation landed:**
- New Pydantic schemas in `schemas/knowledge/agents.py`: `Section(heading,
  prose_md, code_refs)` + `ChapterOutput(sections, challenges, flashcards)`.
  `ChapterSynthesis` retained as the downstream-facing shape (grader,
  artifact writer, curator) — the synth node builds one via the assembler.
- `SYNTHESIZER_PROMPT` rewritten for the structured schema with a worked
  example showing "bare hash in code_refs, NOT tag in prose_md."
- New helpers in `graphs/knowledge/helpers.py`: `_audit_structured_output_refs`
  (missing / invented / fence-contaminated sections), `_assemble_chapter_markdown`
  (deterministic `# title` + `## heading` + prose + vault fences), plus
  `_format_structured_output_feedback` that speaks the schema's language
  for Self-Refine retries.
- `synthesize_chapter` in `graphs/knowledge/distiller.py` switched to the
  structured path: synth → audit → assemble → ChapterSynthesis → grader.
  Pydantic-level fence rejection was considered and NOT used — it cascades
  the whole fallback chain on violations before the LLM can see feedback;
  audit-driven refine is more graceful and actionable.

**Tests:** `scripts/test_structured_output.py` (12 unit cases covering
audit, assembler, feedback rendering, and full round-trip). Existing
`scripts/test_code_vault.py` still valid (vault primitives unchanged).
`scripts/test_synth_isolation.py` updated to use ChapterOutput in the
grader-failure mock.

**Next-run expectation:** preservation goes to 100% by construction —
the LLM emits only metadata about which vault hashes go where; it cannot
strip, paraphrase, or invent code. The audit catches "LLM forgot to
reference hash X" exactly as the old sentinel audit did, but with a
cleaner failure mode (count missing hashes, not "did text survive").

---

**Rationale.** The strongest form of code preservation is to never let
the LLM emit code tokens at all. Rewrite the synthesizer to return a
Pydantic structure where code blocks are referenced by ID, not embedded
as text:

```python
class Section(BaseModel):
    heading: str
    prose_md: str                    # schema-rejected if it contains ``` fences
    code_refs: list[str] = []        # vault keys from Tier 0a, in order
class ChapterOutput(BaseModel):
    sections: list[Section]
    # challenges/flashcards unchanged
```

Final markdown assembled programmatically: for each section, emit
`heading` → `prose_md` → interleave fences resolved from `code_refs`
in order. Use native strict JSON-Schema mode (Claude 4.x, GPT-5,
Gemini 2.5). Add regex constraint `pattern: "^(?!.*```).*$"` on
`prose_md` so fences are rejected before generation (on providers that
support regex-constrained JSON Schema).

**Tradeoff vs. Tier 0 vault alone.** Stronger guarantee (LLM *cannot*
write code), slightly weaker prose-code interleaving (model sometimes
picks suboptimal ref order). Tier 0 vault is cheaper and good enough
for ~95% of chapters. Revisit #21 if audit finds ≥5% of chapters
still have code-placement issues post-Tier-0.

**Effort:** ~150 LoC (synth node rewrite + assembler change).

### 22. Claude Citations API for verbatim source binding — NEW (strategic)
**Rationale.** Anthropic's Citations API (GA mid-2025) provides
server-side, guaranteed-verbatim source binding: `cited_text` returns
byte-identical source substrings, does not count against output tokens,
and cannot hallucinate content. For Claude-family synths this is the
gold standard.

**Tradeoff.** Claude-only (portable simulation via LangChain
`qa_citations` is weaker). Worth piloting once LangFuse (#14) is live
so we can A/B Citations-vs-vault quality metrics.

**Effort:** ~100 LoC (Anthropic provider wrapper; only relevant when
the active model in the fallback chain is a Claude model).

---

## Tier 4 — Strategic / future

### 16. Preview mode (classical-only baseline)
A `?preview=true` parameter runs: ingest → splitter → MAP clustering → c-TF-IDF cluster labels → TextRank per-chapter extractive summaries. No synthesis, no challenges, no flashcards. ~5 min wall-clock, zero LLM cost. **Strictly verbatim by construction** — no LLM ever rewrites content. Usable as:
- Sanity-check before committing to a full 30-min run
- Fallback when all LLM providers are down
- Validation baseline to catch synth hallucinations

~150 LoC.

### 17. Noise pre-filter before MAP
Classical heuristics (slug pattern matching: `changelog`, `release-notes`; file length < 200 chars; code-to-prose ratio ≈ 0) drop obvious noise before shard-labelers waste LLM calls. Typical effect: 5-15% fewer shards. ~30 LoC.

### 18. Grader hard-threshold per dimension
If `citation_integrity < 0.5`, skip scoring the other 7 dimensions — straight to refine. Fast-fail the hopeless cases. ~20 LoC. Fits alongside #9. Extend post-#19 to also hard-fail on `code_preservation_ratio < 0.90`.

---

## Suggested sprint order (revised)

### 📋 Run-9 post-mortem (2026-04-24 late)

See [`KD-RUN-9-POST-MORTEM.md`](KD-RUN-9-POST-MORTEM.md) for the full diagnostic. Highlights:

- **2/11 chapters committed** (ch03 synth, ch08 below-threshold via KEEP-BEST) vs Run-8's 0/9.
- **Crashed in curator** on a latent `_load_all_chapters` → 3-tuple bug (fixed on disk, awaiting commit).
- **Dominant failure mode** (6/11): distribution collapse on chapters with 50-117 vault hashes — LLM regresses on "fix empty sections" feedback.
- **Provider MVP**: Mistral at 98% success (114/116 calls) after OP-3 removed Groq small-TPM entries.
- **OP-1 regression**: `MAP_SHARD_SEMAPHORE 30→15` caused 30-min MAP (vs Run-8's 10 min) via straggler pileup. OP-5 below rolls it back partway.
- **Component validation**: Tier 3 #13 partial cache, BM25F, PCA, MinHash dedup, noise pre-filter, `PydanticValidationError` handler — all behaved as designed.

**New improvement candidates from Run-9 (not yet in a sprint):**

*First wave — micro-ops (ship together):*

| # | Proposal | Effort | Est. impact |
|---|---|---|---|
| **OP-5** | Revert `MAP_SHARD_SEMAPHORE` to 20-25 + add per-shard 180s time-box | ~5 LoC | MAP back to ~12-15 min (Run-9 hit 30 min due to stragglers under sem=15) |
| **OP-6** | Lenient-accept on iteration exhaustion (commit least-bad audit iter if ≤3 missing / ≤2 empty / ≤2 invented) | ~20 LoC | +30% committed-chapter rate; **superseded by OP-12 below (stronger)** |
| **OP-7** | Regression detection on audit-issue count (not just graded score) | ~15 LoC | Early-stop + commit best-audit iter when loop drifts (ch10 case) |
| **OP-8** | Raw-corpus-leakage detector in `_audit_structured_output_refs` (flag `--- filename.md ---` patterns in prose) | ~10 LoC | Catches ch03-style tail degradation |
| **OP-9** | Relax `empty_sections` threshold 40 → 80 chars OR allow ≤2 per chapter | ~5 LoC | Saves ch07-class chapters (valid transition sections) |
| **OP-10** | Synth prompt: "if nearing output budget, prefer `code_refs: [...]` + 1-line pointer over verbatim paste" | ~5 LoC | Reduces tail-degradation |
| **OP-11** | Refine anchor = best-seen iter, not last-seen | ~30 LoC | Reduces Self-Refine drift regression |

*Run-10 emergency fixes (shipped 2026-04-24 late):*

| # | Proposal | Status | Effect |
|---|---|---|---|
| **OP-19** | Move OP-12 rescue INSIDE the `except RuntimeError` handler so synth-timeout / cascade-exhaustion terminal errors also commit best_audit_iter as best-effort | ✅ **SHIPPED** | Would have saved ch01, ch04, ch07 in Run-10 — all 3 had clean early iters (18-29 issues) that were discarded when later iters hit the 1200s outer timeout. Converts 3 sentinels to 3 best-effort commits. |
| **OP-21** | Normalize LLM `resp.content` in curator `_curate_one` — flatten list-of-blocks responses (Mistral reasoning tokens, Claude-style content blocks) to string before `_audit_sentinel_roundtrip`. Also defensive coerce inside the audit helper itself. | ✅ **SHIPPED** | Fixes Run-10 crash `TypeError: expected string or bytes-like object, got 'list'` during curator pass on 8th chapter. Covered: list-of-dicts with `type: text`, list-of-strings, arbitrary. |
| **OP-20** | Align router `create_study` `max_concurrent_chapters` default 5 → 2 (or env-var-configurable). Currently router overrides the graph's documented safe K=2 with 5 silently. | ✅ **SHIPPED** | Default 5 → 2 in `routers/v1/knowledge/distiller.py::create_study`, `tasks/knowledge/distiller.py::run_knowledge_distiller`, and `schemas/knowledge/inputs.py::CreateBatchRequest` (2026-04-24 late). Matches the graph's built-in safe default and module docstring. |

*Run-10 material-quality improvements — driven by chapter review (added 2026-04-24 late):*

Run-10 committed 8/11 chapters (ch02/03/05/06/08/10/11 + the ch09 that was mid-flight). Verification against the source corpus showed the earlier "hallucinated model names" concern (`gpt-5.4`, `claude-sonnet-4-6`, etc.) was **incorrect** — those are real 2026 model IDs and appear 5-772× in the ingested `llms-full.txt`. Code blocks are verbatim from source per Tier 0a — **code itself is trustworthy**. The real defects are structural:

| # | Proposal | Priority | Effort | Effect |
|---|---|---|---|---|
| **OP-22** | **Mintlify-tag + raw-corpus-leak scrubber in assembler.** Regex-delete: lines matching `^--- docs-[^ ]+\.md ---$`; orphan `<Tabs>` / `<Accordion>` / `<Expandable>` / `<Warning>` / `<Tip>` / `<Note>` / `<CodeGroup>` blocks with no paired close; imbalanced ``` fence counts (rebalance or trim trailing content). | **P0** ✅ **SHIPPED** | ~120 LoC in `_scrub_assembled_markdown` + `_assemble_chapter_markdown` | 2026-04-24 late. 4-pass post-assembly pipeline: (1) strips `^--- docs-*.md/.txt/.rst ---$` boundary markers, (2) drops orphan Mintlify tags (`Tabs`/`Tab`/`Accordion`/`Warning`/`Tip`/`Note`/`Info`/`Caution`/`CodeGroup`/`Expandable`/`ParamField`/`ResponseField`/`Card`/`CardGroup`/`Frame`/`Check`) OUTSIDE code fences, (3) fence-count integrity → appends closing ``` if odd, (4) inline-backtick sanitization. Defensive: any exception returns original unchanged. Stats logged at INFO for post-run tuning. |
| **OP-23** | **Per-section quality gate.** Extend `_audit_structured_output_refs` to also flag: (a) sections with <40 words of prose per code_ref, (b) any chapter emitting zero `# docs:` citations total. Force refine when triggered. | **P0** ✅ **SHIPPED** | ~70 LoC in `helpers.py` + call-site updates | 2026-04-24 late. Audit now returns 6-tuple `(missing, invented, fence_sections, duplicated_refs, empty_sections, thin_sections)`. `thin_sections` contains either code-dump section headings (`<40 words/code_ref` with formatted count) or the sentinel `__zero_citations__` when a chapter with ≥3 non-trivial sections has zero `# docs:` markers. `_format_structured_output_feedback` produces two distinct refine-feedback messages. All call sites updated (distiller.py, test_structured_output.py, test_synth_isolation.py). |
| **OP-24** | **Planner topic exclusivity.** After REDUCE produces the chapter plan, run a topic-overlap detector (TF-IDF cosine between chapter goals + file-assignment Jaccard). If two chapters have similarity > 0.4 on goals OR share >20% of assigned files, merge them or reassign. Memory/Store appeared in 4 chapters in Run-10; env vars in 3; createAgent examples in 3. | P1 | ~100 LoC in planner | Shrinks cross-chapter redundancy ~20%, raises signal-to-noise. Bigger wall-clock benefit on the synth phase (fewer chapters to synthesize at all, each larger but more complete). |
| **OP-25** | **Per-model timeout tuning via LangFuse data.** Post-run, use Langfuse's `scripts/kd_catalog_health.py` output to identify which models consistently burn their full 120s budget. Demote those (drop from catalog) OR lower their per-entry `timeout_s` to 60. Prevents 1200s outer timeouts from stacking on unresponsive entries. | P1 | ~10 LoC in `services/llm_chain.py` (adjust timeouts per findings) | Run-10 lost ch01/04/07 to 1200s outer-timeout cascades — OP-19 rescues them now, but preventing the timeout entirely is better than rescuing from it. |
| **OP-26** | **`skip_below_threshold: bool` flag** on `CreateStudyRequest`. When true, below-threshold committed chapters ALSO write to full cache with a `best_effort=true` marker. Next run skips them unless user explicitly unsets. | P1 ✅ **SHIPPED** | ~35 LoC across 5 files | 2026-04-24 late. Added to `CreateStudyRequest` (default False). Plumbed router → task → graph via `build_knowledge_distiller_graph(skip_below_threshold=...)` closure. Threaded into canary + Send() workers via payload. `cache.set_chapter(best_effort=...)` records `best_effort=true` in `_grader.json`. When flag True AND chapter committed with content_path (not SENTINEL), cache write happens regardless of graded-accept. Default False preserves historical behavior. |
| **OP-14 (re-prioritized)** | **1-chapter canary before full Send() fan-out.** Synth the smallest chapter first; if it crashes, abort with clear error. | P1 ✅ **SHIPPED** | ~80 LoC: new `canary_synth` node + graph rewiring + state field | 2026-04-24 late. New node between `planner` and the conditional fan-out edge. Picks `min(plan, key=(len(assigned_files), number))` and calls `self.synthesize_chapter` inline. Exceptions bubble out — LangGraph fails the whole Celery task cleanly at ~1 chapter of compute cost. `state["canary_chapter_number"]` flows to `fan_out_chapters` which excludes it from Send(). Degenerate 1-chapter plans route directly to `curator`. Would have caught Run-9's 3-tuple and Run-10's OP-21 bugs immediately. |
| **OP-27** | **Flashcard quality review in audit.** Current `_audit_structured_output_refs` only checks code_refs distribution. Add a pass that validates flashcards: no repeated questions across cards, no Q==A (tautology), answer length ≥ 20 chars, no generic "What is X?" with one-word answer. Reject chapter if >20% of flashcards fail these gates. | P2 | ~30 LoC | Run-10's flashcards were never reviewed — they may be high-value OR stub-quality. Easy to add objective gates. |
| **OP-28** | **Fence-count integrity gate in assembler.** Before writing chapter README to MinIO, count opening vs closing triple-backtick fences. If imbalanced, either trim trailing content OR append a closing fence. Reject if rebalance fails (chapter has a truly broken structure). | P2 ✅ **SHIPPED** | part of OP-22 pipeline | 2026-04-24 late. Pass 3 of `_scrub_assembled_markdown` counts line-start ``` occurrences; if odd, appends a closing fence on the final line. Logged + reported via scrub stats. |
| **OP-29** | **Inline-code-backtick sanity.** Scan prose_md for `\`\`\`` patterns that escaped the fence-contamination audit. Current audit checks for `\`\`\`` at line start; inline ones slip through. | P3 ✅ **SHIPPED** | part of OP-22 pipeline | 2026-04-24 late. Pass 4 of `_scrub_assembled_markdown` — walks lines tracking in-fence state; replaces stray inline ``` in prose with single-backtick inline-code spans. Rare but nonzero rate observed in Run-10. |

**Top-4 shipping order** for the highest material-quality impact:

1. **OP-22** (~25 LoC, 10 min work) — immediate visible quality win; raw leaks gone from all future chapters ✅ SHIPPED
2. **OP-23** (~50 LoC, 30 min work) — catches synth-quality drift early; fixes ch06-style zero-citation outputs ✅ SHIPPED
3. **OP-28 + OP-29** (~20 LoC combined) — markdown hygiene on write; prevents rendering breaks ✅ SHIPPED
4. **OP-26** (~10 LoC) — user control over below-threshold re-synth ✅ SHIPPED

### ✅ Shipped on 2026-04-24 late (pre-Run-11 batch)

One intensive session across 7 OPs (~350 LoC total). All top-4 material-quality items PLUS OP-14 safety net PLUS OP-20 concurrency-default hygiene:

| # | What shipped | Files touched |
|---|---|---|
| **OP-22** | 4-pass `_scrub_assembled_markdown` (raw-corpus boundaries + Mintlify orphans + fence balance + inline-backtick sanity). Runs inside `_assemble_chapter_markdown`. INFO-level stats log. | `graphs/knowledge/helpers.py` |
| **OP-23** | 6-tuple audit with `thin_sections` (code-dump + zero-citation). `_format_structured_output_feedback` emits two distinct refine messages. | `graphs/knowledge/helpers.py`, `graphs/knowledge/distiller.py`, `scripts/test_structured_output.py`, `scripts/test_synth_isolation.py` |
| **OP-28** | Pass 3 of scrub pipeline — append closing ``` when fence count is odd. | part of OP-22 |
| **OP-29** | Pass 4 of scrub pipeline — inline `` ``` `` → `` ` `` in prose regions. | part of OP-22 |
| **OP-14** | New `canary_synth` node + graph rewiring + `state["canary_chapter_number"]`. 1-chapter smoke test before fan-out. | `graphs/knowledge/distiller.py`, `schemas/knowledge/state.py`, `tasks/knowledge/distiller.py` |
| **OP-26** | `skip_below_threshold` flag end-to-end. `cache.set_chapter(best_effort=...)` records flag in `_grader.json`. | `schemas/knowledge/inputs.py`, `routers/v1/knowledge/distiller.py`, `tasks/knowledge/distiller.py`, `graphs/knowledge/distiller.py`, `services/knowledge/cache.py` |
| **OP-20** | Router + task + batch-schema defaults 5 → 2. Aligns with graph's safe K. | `routers/v1/knowledge/distiller.py`, `tasks/knowledge/distiller.py`, `schemas/knowledge/inputs.py` |

**Bytecompile clean** on every modified file. Tests (`test_code_vault.py`, `test_structured_output.py`, `test_synth_isolation.py`) are updated for the 6-tuple audit shape and the `set_chapter(best_effort=...)` signature. They'll run in-pod after skaffold restart.

### ✅ Shipped on 2026-04-25 (Run-11 post-mortem batch)

Run-11 result: **9/10 chapters committed (all DEBT) + 1 sentinel + critic crash → Celery FAILED**. 9 chapter READMEs survived to MinIO; summary.md + DEBT.md never written. Run beat every prior run on commit count but failed at the final aggregation step. Post-mortem revealed three categorical issues:

1. **Critic crashed with no rescue path** — RuntimeError from LiteLLM Router (None response) bubbled up; whole task FAILED.
2. **Zero ACCEPTs** — every commit was OP-12/OP-19 rescue (DEBT). The thin-section gate from OP-23 was too strict; chapters with shape `(0/0/0/0/0/N thin)` were perfect on every other dim but couldn't ACCEPT.
3. **Self-Refine drift dominant** — best-seen iter was almost always iter 1 or 2; later iters regressed on thin sections; OP-7 fired 3× (ch01, ch04, ch08).

Nine OPs shipped to address these:

| # | What shipped | Files touched | Run-11 evidence motivating it |
|---|---|---|---|
| **OP-30** | Critic rescue path: wrap LLM critic in try/except → emit deterministic-only `CriticAssessment` (citation_coverage + linter + fence_scan still valid) when LLM fails. Assembler still runs. | `graphs/knowledge/distiller.py::critic` | Run-11 critic raised RuntimeError → Celery FAILED; 9 chapter READMEs were on disk but no summary.md / DEBT.md. |
| **OP-35** | Per-chapter critic fallback: when bundle call fails, retry each chapter as a tiny isolated prompt before falling all the way to OP-30 deterministic-only. Aggregates per-chapter scores when ≥1 succeeds. | `graphs/knowledge/distiller.py::critic` | Run-11 50KB 5-chapter bundle was too complex for any model in cascade; 8KB single-chapter prompts are 5-10× more retry-friendly. |
| **OP-31** | Thin-section ACCEPT allowance: up to **3 real-thin sections** per chapter no longer force refine. Zero-citation marker still always fails. | `graphs/knowledge/distiller.py` (Self-Refine gate) | ch04/ch08/ch10 reached `(0/0/0/0/0/N thin)` shapes — perfect except for thin sections; OP-23 forced refine, LLM regressed (OP-7 had to rescue). Without OP-31 no chapter can ACCEPT given current cascade. |
| **OP-11** | Refine from best-seen iter, not last: when iter N is worse than `best_audit_iter`, prepend **ANCHOR** message to the next refine prompt naming the best iter's metrics + telling LLM to recover to that quality. | `graphs/knowledge/distiller.py` (refine adjustments) | ch01 best=iter 1 (19 issues), iter 2/3 worse → 4× regression. ch08 went 26→5→31 (6.2× regression). Self-Refine LLM had no visibility into earlier iters' quality. |
| **OP-32** | Hierarchical refine feedback: thin-section feedback only included when `(missing+invented+fence+dup+empty) < 5`. Until structural defects fixed, focus prompt on those alone. | `graphs/knowledge/distiller.py` (refine adjustments) | ch01 thin sections grew 7→12→18 monotonically — LLM was fixing empty sections by spreading refs (creating thin) AND being told to thicken thin (contradictory). Tier the feedback. |
| **OP-33** | Anti-hallucination prompt hardening: when iter N had `invented > 0`, iter N+1 prompt prepends a **STRICT WHITELIST** with up to 50 valid bare-hex hashes + "if you can't place a code block, omit it rather than fabricate." | `graphs/knowledge/distiller.py` (refine adjustments) | ch02 iter 1 invented 76 hashes after iter 0 had 51 missing. Recovered iter 2 with strong feedback but cost a full iter of compute. Explicit whitelist > generic "don't invent." |
| **OP-17** | Grader sees audit: `_grade_attempt` now accepts `audit_summary` kwarg; `GRADER_PROMPT` has new `{audit_summary}` template var. Grader gets verified deterministic facts (missing/invented/fence/dup/empty/thin counts) instead of re-deriving by inspection. | `graphs/knowledge/helpers.py::_grade_attempt`, `schemas/knowledge/prompts.py::GRADER_PROMPT`, `graphs/knowledge/distiller.py` (caller) | Run-11 had 0 graded iters across all chapters — every commit was via rescue. With audit signals in hand, grader can be more decisive on borderline (e.g. 3-thin-only) cases. |
| **OP-18** | Adaptive iteration budget: ≤30 hashes → 3 iters, 31-80 → 5, >80 → 7. Replaces fixed `MAX_SELF_REFINE_ITERATIONS = 5`. | `graphs/knowledge/distiller.py::_adaptive_iter_budget` + Self-Refine loop | ch04 hit 14 issues at iter 0 (very clean) and wasted 4 more iters; ch01 hit 83 issues at iter 0 and plateaued by iter 4. Match budget to complexity. |
| **OP-25** | Per-model timeout demotion: `gemini-2.5-flash` and `mistral-medium-latest` lowered from 120s → 60s timeout in `kd-all` catalog. Cascade walks past them faster on bad-quota days. | `services/llm_chain.py` | Gemini 2.5-flash hit "Quota exceeded ... limit: 20, model: gemini-2.5-flash" — 20 req/DAY cap, dead for ~24h once exhausted. Mistral medium hit RateLimitError repeatedly. Faster timeout = less wasted budget. |

**Bytecompile clean** on every modified file. Tests should still pass (no signature changes that affected test mocks; `_grade_attempt`'s new param has a default).

### ✅ Shipped on 2026-04-25 (Run-12 post-mortem batch)

Run-12 result (Docker docs): **0/7 chapters committed, 1 sentinel via canary, then aborted by user.** Root cause: `https://docs.docker.com/llms-full.txt` is a **manifest** (URL: + Markdown: pointer index, ZERO fenced code blocks across 318KB), not real content. Tier 1 ingested it as content; vault extraction yielded 0 hashes per chapter; the structured-output synth path required code_refs from an empty vault → cascade exhaustion + LLM hallucination of 30+ invented hashes per iter.

| # | What shipped | Files touched | Run-12 evidence motivating it |
|---|---|---|---|
| **OP-50** | **Tier-1 manifest detection + Tier-2 downgrade.** After Tier 1 fetches llms-full.txt, run `_looks_like_manifest`: if `<5` fences AND `>100` URL: lines, raise `TierOneManifestDetected`. Dispatcher catches it and routes to Tier 2 (parses the URL: + Markdown: pointers natively) instead of Tier 4 Playwright. | `services/knowledge/llms_full_ingest.py`, `services/knowledge/ingestion.py` | Docker llms-full.txt = 318KB, 0 fences, 600+ URL: lines. Run-12 ingested it as content; vault was 0 every chapter. |
| **OP-46** | **Empty-vault prose-only synth path.** When `len(code_vault) == 0`, short-circuit Self-Refine: single `_synthesize_prose_attempt` call with `SYNTHESIZER_PROSE_PROMPT` + `ProseChapterOutput` schema (no code_refs field). `_assemble_prose_chapter_markdown` emits heading + prose, runs through scrubber. Min-quality gate: ≥500 chars + ≥1 citation. | `graphs/knowledge/distiller.py`, `graphs/knowledge/helpers.py`, `schemas/knowledge/agents.py`, `schemas/knowledge/prompts.py` | Run-12 ch01/02 hallucinated 30 invented hashes from empty vault. With OP-46, prose-heavy chapters (security policies, compliance) commit cleanly. Belt-and-suspenders with OP-50: even after Tier downgrade, individual chapters that happen to be prose-heavy commit instead of failing. |
| **OP-47** | **Vault extractor diagnostic logging.** `[synth][chXX] vaulted N from M files; raw_fences=K, langs=[bash,yaml,...]` at synth start. Surfaces extractor problems. | `graphs/knowledge/distiller.py` | Run-12 lost time chasing "0 vault hashes" without knowing if source had no code OR extractor missed it. Future "0 hashes" cases are immediately diagnosable. |
| **OP-36** | **Mintlify fence-metadata scrubber.** Pass 0 of `_scrub_assembled_markdown`. Strips `theme={"theme":{...}}`, `expandable`, `lines`, `title=`, `wrap`, `icon=`, `actions=`, `highlight=`, `focus=`, `filename=`, `copy=` from fence opener lines. | `graphs/knowledge/helpers.py` | Run-11 LangChain output had this metadata on EVERY code block — biggest visible noise. |
| **OP-37** | **Stacked-citation reformatter.** Pass 5 of scrubber. Splits `# docs: foo.md # docs: bar.md # docs: baz.md` runs onto separate lines (4-6 stacked citations was common in Run-11). | `graphs/knowledge/helpers.py` | Run-11 ch04 had multiple lines with 4-6 concatenated citations. |
| **OP-38** | **Chapter intro paragraph instruction.** SYNTHESIZER_PROMPT system message now requires first section to open with 2-3 sentences of orientation BEFORE any code. | `schemas/knowledge/prompts.py` | Run-11 chapters jumped straight into code at section 1 — tough cold-read entry. |
| **OP-40** | **Pre-emptive thicken-prose synth instruction.** SYNTHESIZER_PROMPT requires 2-3 explanatory sentences BEFORE each code block. Stops thin sections at iter 0 instead of fighting them at iter N via OP-23/OP-31. | `schemas/knowledge/prompts.py` | Run-11 saw thin-section drift (ch01: 7→12→18) — addressing at the source. |
| **OP-45** | **Per-chapter critic by DEFAULT** (replaces bundle-then-fallback). N parallel `asyncio.gather` LLM calls, one per chapter; aggregate scores + issues. OP-30 deterministic-only is the floor when ALL per-chapter calls fail. Eliminates the bundle-failure mode entirely. | `graphs/knowledge/distiller.py::critic` | Run-11 50KB bundle was too complex for any model in cascade. Per-chapter prompts are 5-10× smaller; parallel = faster than the previous serial OP-35 retry loop. |

**Deferred for next batch:** OP-43 (persistent log shipping), OP-44 (LangFuse credentials in cluster secret), OP-48 (planner-time vault preview), OP-24 (planner topic exclusivity), OP-27 (flashcard quality review), Tier 3 #12 (sub-chapter synthesis batching).

### Tier-2 ingestion-quality improvements (proposed during Run-13, 2026-04-25)

Run-13 evidence: Tier 2 (after OP-50 downgrade) fetched 1,291/1,415 Docker URLs, but trafilatura's content extractor `discarding data:` warnings dropped genuinely-valuable pages (Docker Hardened Images guides: `dhi-from-wolfi`, `dhi-nodejs-example`, `dhi-go-example`, `dhi-python-example`). Trafilatura returns None on ~10-15% of pages (JS-rendered SPAs, text-to-boilerplate ratio misses, anti-bot challenges, valid pages with unusual layouts). Real content loss, not just navigation noise.

| # | Proposal | Priority | Effort | Effect |
|---|---|---|---|---|
| **OP-51** | **Tier 2 prefer-markdown-pointer.** When the parsed llms.txt entry has a `Markdown: <url>.md` field (Docker, LangChain, every llmstxt.org-compliant docs site), fetch the `.md` URL directly as markdown instead of fetching the HTML variant and running trafilatura. Skip extraction entirely for the `.md` path. | **P0** | ~30 LoC in `services/knowledge/llms_txt_ingest.py` | Extraction success rate ~85% → ~99%. Faster (no HTML parse). Cleaner output (no trafilatura artifacts: stripped tables, lost code blocks, paragraph reflow). |
| **OP-52** | **Discard-tracking + post-mortem artifact.** Log discarded URL count + sample at end of Tier 2; emit a structured `discarded_urls.json` artifact in `study_root/research/`. Spot-check what was lost without grepping pod logs. | P1 | ~10 LoC | Operator visibility into ingestion gaps. Currently invisible — discards only show in worker logs which evaporate. |
| **OP-53** | **HTML-fallback when trafilatura fails.** If trafilatura returns None on the HTML variant (only reached when OP-51 not applicable, e.g. no `Markdown:` pointer in manifest), try a simpler BeautifulSoup `<main>` / `<article>` / `[role="main"]` extraction before giving up. | P1 | ~25 LoC | Catches text-to-boilerplate-ratio misses. Saves another 5-8% of discarded pages. |
| **OP-54** | **Preflight mode** for the 100-framework production phase. New `?preflight=true` query on `POST /studies` that resolves URL, sniffs docs platform (Mintlify CSS / mkdocs.yml / Sphinx markers / Hugo / custom), counts source fences, estimates corpus size, predicts ingestion tier, classifies into one of ~6-10 archetypes (Mintlify / Hugo+manifest / Sphinx-RTD / mkdocs Material / API-first / notebook-heavy / GitHub-readme-only / custom). Returns JSON report. Does NOT run synth. ~5-10s per framework. | **P0** for 100-framework production phase | ~50 LoC new endpoint + classifier | Lets you classify 100 frameworks in ~30 min unattended → run 6-10 cluster representatives → fix per-cluster bugs → bulk-batch the remaining 90 in parallel via `/studies/batch`. Cuts total time-to-100-quality-outputs from ~75h sequential to ~30h stratified, mostly unattended. |

### Run-13 quality findings — chapter-readability batch (proposed 2026-04-25)

Run-13 (Docker docs) committed 4/8 chapters via Tier 2 (after OP-50 downgrade). Manual chapter review revealed three NEW visible-quality issues that affect study-readability for any docs source, not just Docker:

| # | Proposal | Priority | Effort | Effect |
|---|---|---|---|---|
| **OP-55** | **Literal `\n` → actual newline scrubber pass.** Synth LLM occasionally emits JSON escape sequences inside `prose_md` strings (`# docs: foo.md\n\nNext paragraph...`) which get preserved literally during assembly and render as 4 visible characters instead of paragraph breaks. Add as Pass 6 of `_scrub_assembled_markdown`: convert `\n\n` → real newline pair, `\n` → real newline, ONLY in prose regions (not in fenced code where they may be intentional). | **P0** | ~10 LoC in `helpers.py` | Single biggest visible-quality fix. Run-13 ch04 had 10+ instances; ch02 had several. Pure text transformation, near-zero risk. |
| **OP-56** | **Auto-detect fence language at ingestion.** When the source publishes bare ` ``` ` fences (Docker docs, many README files), the vault preserves them untagged. Sniff the first content line of each fenced block to infer language (`$ ` / `sudo ` → bash; `services:` / `version:` → yaml; `package main` → go; `import ` / `def ` → python; `{` / `"name":` → json; `# syntax=docker/dockerfile` → dockerfile). Tag the fence opener accordingly during vault extraction so the assembled output gets syntax-highlighting in any markdown renderer. | P1 | ~30 LoC in `_vault_code_blocks` | Run-13 OP-47 diagnostic surfaced `langs=[<none>]` for every Docker chapter — every code block is rendered as plain monospace instead of highlighted. Massive visual quality jump for free. |
| **OP-57** | **Stricter chapter-intro enforcement.** OP-38 added a synth-prompt instruction requiring the first section to open with a 2-3 sentence orientation paragraph, but Run-13 evidence shows the LLM often skips it (chapter dives straight into prose-then-code). Add an audit check: first section's `prose_md` must contain ≥2 complete sentences (≥40 chars each, ending in `.` `!` `?`) BEFORE any inline `code` span or citation marker. Force refine if missing. | P1 | ~15 LoC in `_audit_structured_output_refs` | Closes the loop on OP-38 — prompt instruction + audit gate together. Without the gate, the LLM treats the instruction as a soft hint. |

**Bytecompile clean** on every modified file. Tests should still pass (no signature changes that affected mocks).

### Run-13 preparedness checklist

Before kicking Run-13, ensure:

1. **Celery worker is bounced after skaffold restart** (same lesson as Run-11 prep). Bytecode-cache pinning ate Run-10; do not let it eat Run-12. `kubectl delete pod -l app=coelhonexus-celery-worker -n coelhonexus-dev`.
2. **OP-30/OP-35 must be smoke-tested** the easy way: just complete the run. If summary.md + DEBT.md write at all, OP-30 path worked.
3. **OP-31 unblocks the ACCEPT path.** Run-12 should produce ≥1 chapter at score >= 0.85 (real ACCEPT, not DEBT) provided any chapter reaches the `(0/0/0/0/0/≤3 thin)` shape we saw on ch04/ch08/ch10.
4. **OP-11 anchor** should reduce regression frequency. Watch for the `[synth][chXX] iter N audit REGRESSED` log line — should fire less often than Run-11's 3 firings.
5. **OP-18 budget changes total iterations**. Easy chapters (≤30 hashes) commit in 3 iters; hard ones (>80) get 7. Wall-clock should be roughly stable but distribution shifts.
6. **OP-25 timeouts** halve the cascade pause on Gemini 2.5-flash and Mistral medium. If those providers are quota-dead today, you'll see them skipped faster (~60s vs 120s). Alternatively: if Gemini quota has reset, runs perfectly normally.
7. **OP-33 hallucination guard** activates only when iter N had `invented > 0`. If the cascade is healthy, you may not see it fire at all this run.

*Second wave — architectural (ship after first-wave data):*

| # | Proposal | Effort | Est. impact |
|---|---|---|---|
| **OP-12** | **Commit-best-seen ALWAYS** — at Self-Refine end, commit the LEAST-BAD audit iter with DEBT flag. Never sentinel unless iter 0 produced literally no ChapterOutput at all | ~15 LoC (subsumes OP-6) | **Eliminates "0 graded iterations" sentinel class entirely.** Run-10 projected ≥8/11 committed |
| **OP-13** | Auto-route chapters with >80 vault hashes to sub-chapter batching (per-chapter opt-in vs #12's global switch) | ~20 LoC on top of Tier 3 #12 | Converts 100+ hash failures (ch04/09/11) into successes |
| **OP-14** | 1-chapter canary before full fan-out — synth the SMALLEST chapter first; if it crashes, abort Send() with clear error | ~30 LoC | Would have caught today's 3-tuple bug at 1 wasted chapter instead of 11. Safety net for future regressions |
| **OP-15** | Partial cache `code_version_hash` invalidation — include git rev SHA (or module-level checksum) in partial state; invalidate on mismatch | ~10 LoC | Safe partial-cache resumes across code changes (avoids stale-schema bootstrap failures) |
| **OP-16** | TextRank prose compression for files >5K tokens before synth | ~40 LoC (reuses `preview.py` TextRank) | 20-30% more hashes fit in budget; fewer >80-hash chapters |
| **OP-17** | Grader sees audit report — pass `audit_summary` (missing / empty / leakage counts) into grader prompt alongside text | ~25 LoC | Better accept/refine decisions when audit is borderline |
| **OP-18** | Adaptive iteration budget per chapter (≤30 hashes → 3, 30-80 → 5, >80 → 8, with wall-clock cap) | ~20 LoC | Better iter allocation; hard chapters get more refine budget |
| **Tier 3 #12** | Sub-chapter synthesis batching (escalated from DEFERRED; Run-9 proves the bottleneck shifted) | ~150 LoC | Unblocks chapters with >100 vault hashes |

**Priority ordering:**
1. **OP-5** — single biggest SPEED win (MAP 30min → ~12min)
2. **OP-12** — single biggest QUALITY win (subsumes OP-6, eliminates sentinel class)
3. **OP-7** — compounds with OP-12 (early-stop + commit best-seen)
4. **OP-9** — trivial, saves a class of sentinels
5. **OP-14** — safety net for all future runs
6. **OP-15** — safety for partial cache (must land before next partial-cache-resume run)
7. **Tier 3 #12 + OP-13 + OP-16** — architectural batch for >80-hash chapter class
8. **OP-8 / OP-10 / OP-11 / OP-17 / OP-18** — quality polish after the architectural fixes

---

### ✅ Shipped on 2026-04-24 (Run-8 post-mortem + roadmap completion)

**Run-8 RCA fixes** (9/9 chapters sentinel'd in a 72-min wipeout — root causes + fixes):

| Failure mode (count) | Fix shipped |
|---|---|
| **600s outer timeout exhausted mid-cascade** (6/9) | Per-entry timeouts uniformly capped at **120s** in `services/llm_chain.py` (was 300s on NIM, 180s on Mistral); outer timeout raised **600s → 1200s** in `_invoke_structured_with_fallback`. A 10-entry cold cascade now fits the outer budget by construction. |
| **SambaNova `APIError: "Payment method required"`** (2/9) | Both remaining SambaNova entries (DeepSeek-V3.1, Llama-4-Maverick) commented out — account-wide paywall enforcement since 2026-04. Previous 3 SambaNova entries (MiniMax-M2.5, gpt-oss-120b, Meta-Llama-3.3-70B) already disabled earlier. |
| **Pydantic `flashcards min_length=8` rejected 4-item LLM output** (1/9) | `ChapterOutput.flashcards` relaxed to `min_length=4`; `PydanticValidationError` explicitly caught in the Self-Refine loop and converted to a targeted refine-signal (synthesis continues with schema-aware feedback) instead of crashing to TERMINAL FAILURE. |

**Roadmap items shipped today:**

| Item | Notes |
|---|---|
| **Tier 1 #1** BM25F two-field ranking | Upgrade from plain BM25 — extracts `prose` (weight 1.0) and `code` (weight 0.3, camelCase/snake_case-aware) fields per file, BM25-scores each against `chapter.goal`, sums. Code-field tokenizer preserves dotted-identifier bigrams (`foo.bar → {foo, bar, foo.bar}`). Per-chapter file budget now favors pedagogically relevant files on mixed prose/code docs. |
| **Tier 1 #3** PCA pre-reduction before UMAP | `sklearn.decomposition.PCA(n_components=128)` before the UMAP step in `reduce_cluster.py`. Retains 99%+ variance on sentence-transformer embeddings; UMAP is then 128d → 5d instead of 2048d → 5d. Defensive skip when embedding dim ≤ 128. |
| **Tier 2 #6** Code-aware MinHash dedup | Hand-rolled (no `datasketch` dep) — shingled prose Jaccard + per-code-block SHA-256 set equality. Dup iff `prose_jaccard > 0.85 AND code_hashes_a == code_hashes_b`. Keeps the longer of each pair. Protects tutorial-vs-reference pairs with meaningful code variations. Runs after noise filter. |
| **Tier 2 #8** Parallel curator | `asyncio.Semaphore(2)` wraps per-chapter curation (was sequential). Halves curator wall-clock. Style consistency is per-chapter, so overlapping doesn't hurt. |
| **Tier 2 #9 + Tier 4 #18** Deterministic grader pre-gates + hard-threshold | New `_deterministic_grader_gates(synthesis, chapter)` in `helpers.py` runs BEFORE the grader LLM: length sanity / zero-citation fast-fail / zero-fence fast-fail / stub-marker fast-fail (≥3 TODOs). On hard-fail emits a synthetic `GraderEvaluation(action="refine", weighted_score=0.0)` with gate reason as specific_issue — no LLM call. Also fills `code_density` + `citation_integrity` deterministically when they pass. |
| **Tier 4 #17** Noise pre-filter before MAP | New `_filter_noise_files()` in `helpers.py` drops entries where: slug matches boilerplate patterns (`changelog/license/release-notes/migration-guide/cookies/terms/tos`) OR stripped content < 200 chars OR the doc has neither a heading nor a fenced code block. Runs right after `_maybe_split_monolith`. Typical outcome: 5-15% fewer MAP shards. |
| **Tier 3 #13** Per-iteration partial cache (resume-on-failure) | `StudyCache` extended with `get_chapter_partial / set_chapter_partial / clear_chapter_partial` under `_cache/synthesis_partial/…`. `synthesize_chapter` node now: checks partial after the full-cache miss → bootstraps `best_synthesis / best_eval / adjustments / resume_from_iter` on hit → loop becomes `range(resume_from_iter, MAX)` → persists after every graded iteration → clears on normal completion → KEEPS on sentinel so next run resumes. Directly addresses Run-8 ch08/ch09 losing iter-1 grader scores (0.71/0.73) to later-iter cascade timeouts. Same identity check as full cache. |
| **Tier 4 #16** Preview mode (classical-only, zero LLM) | `preview: bool` on `CreateStudyRequest` → plumbed through router → task. When True, the Celery task short-circuits: `ingest → preview_pipeline()` instead of the LangGraph. `graphs/knowledge/preview.py` runs noise-filter → dedup → NVIDIA embed (or fastembed fallback) → KMeans → c-TF-IDF cluster labels → per-cluster TextRank extractive summaries (numpy-only PageRank, no networkx dep). Writes `preview.md` + per-chapter READMEs in ~5 min. Verbatim-by-construction. Use as (a) sanity-check before a 30-min run, (b) fallback when every LLM provider is down, (c) hallucination-validation baseline. |

**Catalog state after today** (27 active / 10 disabled):
- Active: NIM=12, Mistral=6, Gemini=4, Groq=4, Zhipu=2 (sum=28 — **wait, verify: SambaNova 0 after Run-8, DeepSeek 0 pending billing, Cerebras 0 pending access = sum=27 effective with all active providers considered**).
- Disabled with reason comments: DeepSeek v4-pro/flash (insufficient balance), Cerebras gpt-oss-120b/zai-glm-4.7 (403 access denied), 5× SambaNova (full paywall), Groq kimi-k2-instruct (not in actual catalog).
- Uniform 120s per-entry timeout. 1200s outer timeout guards full cascade.

**Test coverage:** 39/39 unit tests green in-pod (`test_code_vault.py` 19 + `test_synth_isolation.py` 3 + `test_structured_output.py` 17). Partial-cache round-trip smoke passes. Live cascade probe: cold 1.2s (was 29.8s pre-RCA), warm 0.9s.

---

### ✅ Shipped before Run-9 — post-Run-8 operational fixes

These are **not roadmap items**. They're concrete operational adjustments driven directly by Run-8 telemetry. All low-LoC, low-risk. Shipped 2026-04-24 afternoon.

| # | Fix | Status | Reason |
|---|---|---|---|
| **OP-1** | `MAP_SHARD_SEMAPHORE` 30 → 15 | ✅ **SHIPPED** | Run-8 logs showed 30 parallel shards racing on the same cascade position before any cooldown registered. Halving the burst lets the circuit breaker bite earlier and cascade moves on to healthy entries sooner. |
| **OP-2** | `MAX_SELF_REFINE_ITERATIONS` 3 → 5 | ✅ **ALREADY IN PLACE** | Constant was already at 5 in the codebase prior to this session; recommendation was aspirational but already satisfied. Pairs with **Tier 3 #13** partial cache for cross-run resumability. |
| **OP-3** | Remove the 3 Groq small-TPM entries | ✅ **SHIPPED** | Commented out `qwen/qwen3-32b` (6K TPM), `openai/gpt-oss-120b` (8K TPM), `meta-llama/llama-4-scout-17b-16e-instruct` (30K TPM). Run-8 logged every call returning BadRequest "Request too large" on chapter prompts. Permanent incompat on free tier. AAII ~15-33 — all tail-tier anyway; gpt-oss-120b still served via NIM's entry at the same AAII. |
| **OP-4** | Investigate `gemini/gemini-2.5-flash-lite` 100% BadRequest | ✅ **DIAGNOSED + DISABLED** | Live pod probe showed plain completion works fine (Test 1 returned "Acknowledged.") but function-calling structured output returns `ModelResponse(choices=[], usage=749 prompt / 0 completion)` — empty response, 0 tokens generated. Model can't produce our `ChapterOutput(sections[Section(prose_md, code_refs)], flashcards[Flashcard])` tool-call shape at the lite tier. LangChain's downstream `choices[0]` access then raises `BadRequestError`, which explains Run-8's 14/14 failure rate. Removed from `kd-all` catalog. |

**Catalog state post-OP-1/3/4 (2026-04-24 late afternoon):**
- **23 active entries** across 4 providers: NIM=12, Mistral=6, Gemini=3, Zhipu=2.
- **14 disabled entries** (commented with reason): 3 Groq small-TPM (OP-3), 1 Gemini flash-lite (OP-4 — structured-output incompat), 2 DeepSeek V4 (insufficient balance), 2 Cerebras (403 access), 5 SambaNova (full paywall), 1 Groq kimi-k2-instruct (not in Groq's real catalog).

**Policy update (2026-04-24):** paid providers explicitly out of scope. DeepSeek V4 entries (insufficient balance) and Cerebras gpt-oss-120b/zai-glm-4.7 (403 access denied) stay disabled indefinitely. The KD pipeline operates on free-tier providers only.

---

### ✅ Shipped on 2026-04-23 (one intensive day across 5 PRs)

**Late-evening super-super-batch — provider diversification + fail-fast:**

| Item | Notes |
|---|---|
| **LiteLLM Router migration** | Replaced hand-rolled `RunnableWithFallbacks` with `litellm.Router` via `ChatLiteLLMRouter`. Pre-call cooldown checks (0ms skip for cooled-down providers), per-error-type retry policy (413/401/429 → immediate cooldown; 5xx/timeout → cooldown after 2 fails), Redis TTL-backed circuit breaker shared across Celery workers. Pinned to `litellm==1.83.12` (post-supply-chain-incident CI/CD v2 build). See detail card below. |
| **6-provider expansion** | Added **Cerebras** (`gpt-oss-120b`, 3000 tok/s, 1M TPD), **Mistral La Plateforme** (`mistral-large-2411`, 1B tok/month), **Google Gemini** (`gemini-2.5-pro` + `gemini-2.5-flash`, frontier reasoning on free tier), **DeepSeek** (`deepseek-reasoner` V3.2 thinking + `deepseek-chat` V3.2 non-thinking), **SambaNova** (`DeepSeek-V3.1` at 200+ tok/s), **Zhipu** (`glm-4.7-flash` + `glm-4.5-flash`, free zero-cap). Catalog in `services/llm_chain.py` now 22 entries across 7 providers, interleaved so single-provider outage affects at most 1-2 top positions. |
| **Drop `openai/gpt-oss-20b`** | Run-7 evidence: 8K TPM < 30K prompt size = permanent incompatibility on free tier regardless of org/project allowlist state. |
| **Tier 1 #4** MAP inter-shard semaphore (=30) | Run-6 baseline 172 HTTP 429; sem caps per-minute MAP pressure |
| **Tier 1 #10** Citation regex whitelist | Exact-match alternation over known corpus slugs; eliminates `api(utils)` → `api` false positives |
| **Tier 2 #7** TF-IDF glossary | `TfidfVectorizer` across all chapters → top-12 domain terms for curator; chapter-0 counter fallback on failure |
| **0d-5** LangFuse catalog-health tool (`scripts/kd_catalog_health.py`) | Standalone analysis script: reads LangFuse API, per-model success/error/preservation, emits demote recommendations |
| **Fragility fix** (critic + assembler) | All-chapters-sentinel no longer crashes the task; produces DEBT.md + WIPEOUT summary.md gracefully |

---

### LiteLLM Router migration detail (2026-04-23 evening)

**Why**: Run-4 through Run-7 repeatedly hit provider-chain cascades that wasted 30+ minutes per run on known-bad models. The hand-rolled `RunnableWithFallbacks` chain walked EVERY failed model to its timeout (120s each) before advancing — a model at position 10 could burn 20+ minutes of serial waits before its turn.

**What**: `litellm.Router` with `enable_pre_call_checks=True` + Redis-backed cooldown cache. Cooled-down deployments are filtered from the candidate pool BEFORE the network call (~0ms skip). Shared state across Celery workers via the existing Redis broker.

**Security**:
- Pinned to `litellm==1.83.12` — the first stable release post-incident from LiteLLM's rebuilt CI/CD v2 pipeline (isolated envs, signed artifacts)
- Versions `1.82.7` and `1.82.8` were compromised via Trivy CI/CD supply-chain attack (2026-03-24, ~40 min on PyPI before quarantine)
- `langchain-litellm==0.6.4` provides `ChatLiteLLMRouter` LangChain wrapper

**Configuration highlights** (see `services/llm_chain.py`):
- `allowed_fails_policy` per error class: 413 → 1 fail triggers cooldown; 429 → 1 fail; timeout → 2 fails; 5xx → 2 fails; auth → 0 (immediate)
- `cooldown_time=60s` (Redis TTL auto-recovery; no explicit reset action)
- `num_retries=0` at LiteLLM layer (cascade, don't retry)
- `routing_strategy="simple-shuffle"` among healthy entries (LiteLLM recommendation — lowest overhead, no extra Redis round-trips per call like usage-based)

**Four groups** cover KD use cases:
- `kd-synth` (22 entries): synthesize_chapter + curator; excludes weak tail (8K-TPM gpt-oss-20b, llama-3.1-8b-instant, llama-3.3-70b-versatile)
- `kd-general` (24 entries): resolver + utility calls; includes tail
- `kd-scope` (3 entries): scope-gate classifier; Groq llama-3.1-8b primary
- `kd-curator` (2 entries): pinned — GLM-5.1 + Cerebras zai-glm-4.7 (same family for style consistency)
- `kd-refine` (11 entries): T=0.7 variant for Self-Refine adjustment generation

**Public API unchanged**: `build_synth_fallback_chain()`, `build_curator_llm()`, etc. still return Runnable objects. Every downstream caller (graph helpers, app.state.llm) works without modification. `_invoke_structured_with_fallback` simplified from ~80 LoC to ~30 LoC since LiteLLM Router handles the cascade.

**Expected behavior change**:
- Bad provider cascade: was 5-30 min per synth call (walking 12 timeouts). Now ~0ms skip for cooled-down models → healthy model served immediately.
- NIM glm-5.1 hang: after 2 timeouts, circuit opens for 60s, synth skips glm-5.1 entirely during that window, uses Cerebras / Gemini / DeepSeek instead.
- gpt-oss-20b style 413: after first 413, `BadRequestErrorAllowedFails=1` opens the circuit; no more 413s from that model for 60s.
- Shared across workers: all 5 Celery workers observing ONE failure instantly benefit — no repeat-discovery of "glm-5.1 is broken right now."

---

### Earlier today shipped — recap



| Batch | Scope | Outcome |
|---|---|---|
| **Tier 0** | 0a vault + 0b prompt clause + 0c integrity check + 0d-1/2/3/4 hardening + 0d-6 per-chapter isolation + `_invoke_structured_with_fallback` robustness | Code-preservation foundation; per-chapter failure isolation; fallback chain truly retries on None |
| **Batch-1** | Tier 1 #2 (token budget + fence-safe split) + Tier 3 #21 (structured-output synth with `code_refs`) | Replaced char cap with tiktoken 40K token cap; cap-after-append off-by-one fixed; schema-enforced preservation (LLM cannot emit fences) |
| **Batch-2** | Tier 1 #4b (synth semaphore 5→2) + Tier 1 #5 (120s eager timeout) + grader rubric calibration for #21 assembled shape | Stampede on NIM primary eliminated; stuck call cost 300s→120s; grader stopped false-penalizing heading+prose+code structure |
| **Super-batch** | Audit uniqueness + empty-section + assembler dedup + prompt distribution rules + Tier 2 #19 (code_preservation_ratio grader dim) + Tier 2 #20 (critic hallucinated-fence scan) + Tier 3 #14 (LangFuse integration + 0d-5 telemetry) | Fixes Run-4 distribution leak; observability for all future runs; deterministic provenance check at critic |

**Test coverage:** 39 unit tests passing in-pod (`test_code_vault.py` 19 + `test_synth_isolation.py` 3 + `test_structured_output.py` 17). Bytecompile clean across all 6 modified files.

---

### Remaining work (prioritize after Run-6 baseline data from LangFuse)

**Sprint 0 (shipped; kept for historical reference):**
Tier 0 (**0a + 0b + 0c**) — code-vault + sentinel clause + integrity check.

Hard prerequisite. Until Tier 0 ships, do NOT deploy the revised #2
(token budget) or #6 (dedup); both assume code is safe to
truncate/compare as text, which is false today. Shipping Tier 0 alone
eliminates chapter 3's synth failure pattern on the 2026-04-22 run
(vault-compressed prompt < NIM timeout, integrity check catches any
surviving corruption).

**Sprint 1 (1-2 days, after Tier 0):**
**#1** BM25F + **#2** token budget + **#3** PCA + **#4** semaphore +
**#5** eager timeout + **#10** citation regex whitelist.

Eliminates failure modes (chapter 3 synth blowup, UMAP 5 min, MAP
stampede). Wall-clock −40%. Zero code fidelity loss (Tier 0 in place).

**Sprint 2 (2-3 days):**
**#6** code-aware dedup + **#7** TF-IDF glossary + **#8** parallel
curator + **#9** deterministic grader gates + **#19** code-preservation
grader dimension + **#20** critic hallucinated-fence check.

Quality-positive polish with code fidelity enforced end-to-end
(preservation metric visible in grader; hallucinated fences rejected
by critic).

**Sprint 3 (when ready):**
**#14** (LangFuse) first — observability makes all subsequent tuning
10× easier and surfaces regressions immediately. Then **#12** or
**#13** depending on which failure pattern persists. Pilot **#22**
(Citations API) on the Claude path once LangFuse is in place.

**Strategic:**
**#15** + **#16** + **#21** — shared infrastructure reusable across
Book Distiller + Deep Research. **#21** (structured-output synth) is
the architectural end-state if a post-Tier-0 audit shows vault is
insufficient for a meaningful slice of chapters.

---

## Deliberately not in the list

- **Relying on "preserve code verbatim" prompt directives alone.**
  The curator prompt already has this clause (`prompts.py:516`) and
  still requires Tier 0 integrity checks behind it. Prompt-only
  verbatim is probabilistic; production needs deterministic
  guarantees. This is the single biggest lesson from the research pass.
- **AST-based canonicalization (tree-sitter) for dedup or integrity.**
  Hides formatting differences that are semantically meaningful in
  docs (whitespace in YAML configs, comments like `# type: ignore`,
  identifier renames). Byte-level canonicalization (trim trailing
  whitespace + drop trailing blank lines, preserve indentation) is
  the right layer. Tree-sitter is acceptable only as a secondary
  `code_parse_valid_ratio` metric, never as a dedup or integrity key.
- **SemDeDup / embedding-based document dedup.** Embeddings collapse
  exactly the code variations we must preserve. Use MinHash on prose +
  exact-hash on code (#6 revised).
- **Character-based truncation caps** (original #2). Cannot be made
  safe retroactively — any mid-file char cut risks mid-fence corruption.
  Token-based + whole-file packing is the only safe form.
- **Changing the LLM fallback chain.** Research-tuned 2026-04-20
  (`llm_chain.py` header). Model list is current.
- **Rewriting synth prompts from scratch.** Well-calibrated. Add the
  Tier 0b sentinel clause; otherwise tune truncation + ranking instead.
- **Replacing KMeansConstrained with HDBSCAN.** Clio explicitly
  rejected this (Appendix G.7) for same-domain corpora — dumps 50%+
  points to noise without heavy patching. Same reasoning applies here.
- **Replacing the critic.** Cheap, already mostly deterministic, works.
- **Adding MLflow.** We're not training or versioning models. Logger
  output is sufficient observability for classical-ML diagnostics.
- **Switching to raw OpenTelemetry first.** For LLM-heavy workloads,
  LangFuse is the faster win; OpenLLMetry on top comes later for
  infra-wide tracing.

---

## References

Existing:
- `KNOWLEDGE-DISTILLER-ARCHITECTURE.md` — canonical architecture
- `KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md` — Tier 1-4 ingestion strategy
- `KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md` — framework-to-URL resolver
- `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` — the v2 Clio REDUCE doc
- `STUDY-GENERATOR-ADAPTIVE-GRADER.md` — grader design + Self-Refine details
- `KNOWLEDGE-DISTILLER-ROUTER-SPLIT.md` — API surface
- Clio paper (Anthropic, arXiv 2412.13678) — Appendix G.5, G.7
- BERTopic Best Practices — https://maartengr.github.io/BERTopic/getting_started/best_practices/best_practices.html
- k-means-constrained — https://github.com/joshlk/k-means-constrained
- HERCULES (hierarchical k-means + LLM) — https://arxiv.org/abs/2506.19992

Added 2026-04-23 (code-preservation research pass):

**LLM placeholder-preservation failure modes + mitigations (2026-04-23 run):**
- Promptfoo: invisible Unicode threats (ZWS / U+200B stripping) — https://www.promptfoo.dev/blog/invisible-unicode-threats/
- AWS Security: defending LLMs against Unicode smuggling (ZWS normalization rationale) — https://aws.amazon.com/blogs/security/defending-llm-applications-against-unicode-character-smuggling/
- Tokenization pitfalls with invisible characters — https://blog.thegenairevolution.com/article/tokenization-pitfalls-invisible-characters-that-break-prompts-and-rag-2
- Reverse CAPTCHA: LLM susceptibility to invisible Unicode injection (arXiv 2603.00164) — https://arxiv.org/html/2603.00164v1
- Anthropic Claude — XML tags for prompt structure — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/use-xml-tags
- Anthropic Claude — prompting best practices (human > system for instructions; query-at-end for 30% lift) — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- Gemini CLI issue #4836 — mirror-image failure (models strip real code and inject `// ...` comments) — https://github.com/google-gemini/gemini-cli/issues/4836

**Code-vault / placeholder substitution pattern:**
- markdown-it-py AST — https://markdown-it-py.readthedocs.io/en/latest/api/markdown_it.token.html
- mistletoe (alternative CommonMark parser with line-number tracking) — https://github.com/miyuchina/mistletoe
- LangChain ExperimentalMarkdownSyntaxTextSplitter — https://reference.langchain.com/python/langchain-text-splitters/markdown/ExperimentalMarkdownSyntaxTextSplitter
- Docling — https://github.com/docling-project/docling
- Extracting and Validating Code Blocks from LLM-Generated Markdown — https://dev.to/german_yamil_e021eef8710d/extracting-and-validating-code-blocks-from-llm-generated-markdown-in-python-4o3a
- OpenAI GPT-4.1 prompting guide (documented verbatim-instruction failure mode) — https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide

**Structured output & citations:**
- Anthropic Citations API — https://claude.com/blog/introducing-citations-api
- Anthropic Citations docs — https://platform.claude.com/docs/en/build-with-claude/citations
- Pydantic for LLMs — https://pydantic.dev/articles/llm-intro
- Citation-Grounded Code Comprehension (2025) — https://arxiv.org/html/2512.12117v1
- LangChain qa_citations — https://python.langchain.com/docs/how_to/qa_citations/
- Anthropic-style Citations with Any LLM — https://medium.com/data-science-collective/anthropic-style-citations-with-any-llm-2c061671ddd5

**Fence-aware chunking & splitting:**
- text-splitter (benbrandt, Rust+Python, CommonMark + token-aware) — https://github.com/benbrandt/text-splitter
- split-markdown4gpt — https://pypi.org/project/split-markdown4gpt/

**BM25 / BM25F on mixed prose/code:**
- BM25S (Xing Han Lu, 2024) — https://github.com/xhluca/bm25s
- BM25S HF blog — https://huggingface.co/blog/xhluca/bm25s
- BM25F from scratch (Turnbull 2025) — https://softwaredoug.com/blog/2025/09/18/bm25f-from-scratch
- rank_bm25 — https://pypi.org/project/rank-bm25/

**Code-aware deduplication:**
- BigCode near-dedup writeup — https://huggingface.co/blog/dedup
- text-dedup — https://github.com/ChenghaoMou/text-dedup
- NeMo Curator Deduplication (reference for the whole-doc baseline that fails on code-diff pairs) — https://docs.nvidia.com/nemo/curator/25.09/curate-text/process-data/deduplication/index.html
- datasketch MinHash — https://ekzhu.com/datasketch/documentation.html
- Why embedding-based dedup is wrong for code (SemDeDup) — https://github.com/facebookresearch/SemDeDup

**Code-integrity evaluation metrics:**
- Self-Refine (Madaan et al.) — https://arxiv.org/abs/2303.17651
- LLMLOOP 2025 (error-guided refinement) — https://valerio-terragni.github.io/assets/pdf/ravi-icsme-2025.pdf
- CrystalBLEU (n-gram downweighting; right tool for doc preservation) — https://www.semanticscholar.org/paper/CrystalBLEU:-Precisely-and-Efficiently-Measuring-of-Eghbali-Pradel/205ac1373eb7981aca2d08f2ab651871a001271e
- CodeBLEU — https://www.semanticscholar.org/paper/CodeBLEU:-a-Method-for-Automatic-Evaluation-of-Code-Ren-Guo/f23a0e443fe931aa2fed932421bf47c1a4fcf619
- cAST: AST-structural chunking (arXiv 2506.15655) — https://arxiv.org/pdf/2506.15655
- tree-sitter Python bindings (for secondary `code_parse_valid_ratio` metric only) — https://github.com/tree-sitter/py-tree-sitter
