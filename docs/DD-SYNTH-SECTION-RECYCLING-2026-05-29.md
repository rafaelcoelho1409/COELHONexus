# DD Synth — within-chapter section recycling (post-dedup assessment)

**Date:** 2026-05-29
**Status:** Write-path fixes #1 + #3 + #4 SHIPPED + **VALIDATED on claude-code
2026-05-29** (re-run PASSED — see §6 results). Outline fixes **#2 + #5 SHIPPED
2026-05-29** (uncommitted, await re-run). browser-use generalization check
still pending.

**#2 + #5 shipped 2026-05-29 (outline/digest-side; need a re-run to take effect):**
- **#2a** `synth/outline/service.py` — HARD RULE 7 "scope orthogonality" with
  the ch-04 Session-Management/Remote-Control anti-example (generation-time
  prevention; corpus-independent, the PRIMARY lever).
- **#2b** `synth/outline/node.py` `_detect_semantic_h2_duplicates` — embedding
  cosine 0.78→0.74 **+ lexical content-word backstop** (fires without the
  embedder). Both are SOFT repair signals. `OUTLINE_PROMPT_VERSION` →
  `v3-scope-orthogonal-2026-05-29`.
- **#5** `synth/digest/service.py` routing prompt → single-best-section homing;
  `_OVER_SPREAD_THRESHOLD` 3→2; `DIGEST_PROMPT_VERSION` → `v2-single-home-2026-05-29`.
- **Honest limitation:** the lexical backstop catches near-verbatim twins but
  NOT differently-worded ones (ch-04 twins measured at 0.16 Jaccard < 0.40).
  Those rest on rule 7 (generation) + the 0.74 cosine + #1 (render dedup,
  already neutralizes the code symptom). **Fallback if the re-run still shows
  overlap:** a post-digest section-pool Jaccard merge (two sections routed the
  same sources ⇒ merge) — the definitive signal, deferred as more invasive.

**Shipped 2026-05-29 (write-path, no re-plan needed):**
- **#1 + #4** → `synth/render/service.py::dedupe_and_align_sections` (new), wired
  in `synth/render/node.py` after `build_section_context`, before render. Pure,
  audit-safe (only rewrites `code_block` strings). #1 cross-references recycled
  bodies to their first occurrence; #4 omits zero-overlap misrouted blocks
  (env toggle `KD_RENDER_DROP_MISMATCH`, default on). `RENDER_TEMPLATE_VERSION`
  → `v3-dedup-align-2026-05-29` (invalidates render cache). Logic-tested.
- **#3** → `synth/sawc/service.py` writer prompt rule 9b ("no boilerplate
  recycling"); `SAWC_PROMPT_VERSION` → `v5-no-recycle-2026-05-29`.
**Trigger:** read all 13 Claude Code chapters after the U2–U7 dedup pass +
V1–V6 speed pass ([[project_synth_dedup_pass_2026_05_28]],
[[project_synth_planner_speed_pass_2026_05_28]]) and re-run. Then ran the
same analysis on a second corpus to check whether the remaining defect is
corpus-specific or systemic.

---

## 1. TL;DR

The catastrophic bug is fixed. **Cross-CHAPTER code recycling = 0** on both
corpora (U2 vault-hash dedup holds). Prose is coherent, structure is clean
(H2 → H3 → explanation + code + per-section source citations), real code is
present (not just shell). The chapters are **shippable as a first pass.**

Two defects remain, and the cross-corpus test proves the big one is
**systemic, not a Claude-Code quirk**:

1. **Within-chapter cross-SECTION recycling — ~45% of distinct code blocks
   appear in >1 section of the same chapter** (several 4×). Present at the
   *same rate* on a config-heavy corpus (claude-code) and a python-heavy one
   (browser-use). This is the #1 thing to fix.
2. **Code↔heading mismatch — ~3%, localized** to "catch-all" sections that
   collect leftover snippets. Minor but real (those sections are misleading).

## 2. Empirical results (cross-corpus)

Measured by extracting every fenced code block per (chapter, H3 subsection),
normalizing the body (collapse whitespace), and counting bodies that appear
in >1 distinct section / >1 distinct chapter. Mismatch = code block whose
identifiers have ZERO overlap with its subsection heading+prose.

| corpus | type | chapters | blocks | distinct | x-section dup | x-chapter dup | mismatch |
|---|---|---|---|---|---|---|---|
| `claude-code` | CLI/config-heavy | 13 | 266 | 161 | **73 (45%)** | **0** | 8 (3%) |
| `browser-use` | Python-SDK-heavy | 2 | 62 | 35 | **16 (46%)** | **0** | 2 (3%) |

Languages — claude-code: bash 87 / json 67 / python 47 / ts 24 / yaml 19 /
text 17. browser-use: python 55 / bash 7. (So the language mix is healthy and
corpus-appropriate; the earlier "optimized for shell not code" worry is
resolved — ch-04/ch-05 carry substantial correct TS/Python.)

**Reading of the numbers:** identical ~45–46% cross-section recycling across
two corpora of opposite code character ⇒ the recycling is a property of the
Synth write/outline path, independent of corpus. Cross-chapter dedup (U2)
works and generalizes. Mismatch is low and concentrated, not widespread.

> Caveat: `browser-use` is only 2 rendered chapters and may predate the
> U2–U7 fixes. It is sufficient to show the cross-section pattern is **not**
> claude-code-specific, but the DECISIVE confirmation (does the new code fix
> it on a fresh second corpus?) requires a full re-run — see §6.

## 3. Concrete examples (claude-code)

- **ch-05 Agent SDK** — the `AgentDefinition` dataclass appears VERBATIM in 4
  sections (Overview / Hosting / Migrating / Programmatic Setup); the
  `query(prompt=..., options=ClaudeAgentOptions(...))` quick-start example in
  4; `ResultMessage` in 2; `ClaudeSDKClient` class def in 2. The four H2s are
  all "how to use the SDK," so each pulls the same 4–5 canonical snippets.
- **ch-01 Install/Configure** — `modelOverrides` JSON in 3 sections;
  `containerEnv` block in 3; the `ANTHROPIC_CUSTOM_MODEL_OPTION` export in 2.
- Same shape in ch-07 (marketplace JSON ×4), ch-09 (MCP config ×4), ch-11
  (Bedrock env ×4), ch-13 (telemetry env ×4).
- **Mismatch (catch-all sections):** ch-01 §Installation puts a *theme JSON*
  under "Install via CLI on Linux/WSL", a *notification-hook JSON* under
  "Install on macOS/Windows", a *markdown API-rules file* under "Authenticate
  via terminal login". ch-05 §Migrating puts `PostToolUseHookInput` under
  "Handle task IDs" and `AgentDefinition` under "Task status updates". All
  passed `audit_passed=true`, so the alignment gate isn't catching
  per-subsection mismatch.

## 4. Root cause

Not vault-hash routing (that's deduped now). Two compounding causes:

1. **Topically-overlapping outline sections.** A chapter's H2s are lexically
   distinct but scope-overlapping (ch-05's 4 sections all = "use the SDK").
   The outline semantic-dedup (U6) compared *heading* embeddings, which are
   distinct here; it does not compare section *scope* / intended source pool.
   Overlapping scopes ⇒ each section's digest routes a similar snippet set.
2. **Writer regenerates canonical code per section.** `sawc_write` writes each
   section independently and emits code from its own generation, not strictly
   the section's routed vault hashes. The same logical block regenerated in
   two sections gets two different content hashes ⇒ U2's hash-based
   cross-section dedup never sees them as duplicates. There is **no dedup on
   the generated, normalized code body across sections.**

Mismatch (#2) is a side effect: snippets that don't fit any section's scope
get dumped into the nearest/first section under a loosely-related heading.

## 5. Fixes to ship (ranked) — all validated by the next re-run

1. **[Highest ROI] Cross-section generated-code dedup (render/harmonize
   time).** After all sections of a chapter are written, normalize each code
   body (strip whitespace + comments) and enforce each body in exactly ONE
   section (keep in strongest-relevance / first occurrence; replace later
   occurrences with a "see §X" cross-reference or drop). This is the U2 idea
   applied to GENERATED bodies instead of vault hashes — directly kills the
   ~45%. Lives in `synth/sawc` post-pass or a `book_harmonize`-style chapter
   pass. No re-distillation needed, but observed on re-run.
2. **Outline section-scope disjointness (planner/outline).** Beyond heading
   embedding (U6), gate on *scope* overlap: embed each section's
   heading+intended-content description, and merge/re-scope sections whose
   intended source pools overlap past a threshold. Prevents 4 near-synonym
   sections. Needs the next outline generation (new distillation run).
3. **Writer prompt constraint.** Tell `sawc_write` to emit code ONLY for its
   section's routed snippets and to NOT reproduce code shown elsewhere in the
   chapter ("reference the other section instead"). Pass the set of
   already-used normalized bodies into later sections' prompts. Cheap,
   prompt-level; compounds with #1.
4. **[Low ROI, cheap] Per-subsection CoCoA mismatch gate.** Run the alignment
   /identifier-overlap check at the (H3 heading ↔ code) granularity, not the
   section level; a subsection whose code has zero identifier overlap with its
   heading+prose is dropped or re-headed. Closes the ~3% mismatch + the
   catch-all dumping.
5. **Orphan-snippet handling (chapter_assign/outline).** Snippets that fit no
   section scope should be dropped or explicitly routed, never dumped into a
   catch-all section. Removes the root of the mismatch cluster.

Ship #1 + #3 + #4 together (write-path, no re-plan needed); ship #2 + #5 with
the next Planner run. Bump SAWC + OUTLINE cache versions so the re-run is cold.

## 6. Validation plan (the "test other docs" discipline)

### RESULT — claude-code re-run, 2026-05-29: **PASS**

| metric | prior run | new run (post #1/#3/#4) |
|---|---|---|
| cross-**section** dup | 73 (45%) | **8 (5%)** |
| cross-**chapter** dup | 0 | 0 |
| code blocks | 266 | 165 (+ 89 cross-refs) |
| distinct-body ratio | 61% | **92%** |
| audit pass | 13/13 | 13/13 |
| #4 mismatch omitted | (8 by heuristic) | 0 (nothing left to drop) |

- The 5% residual cross-section dup is **entirely sub-threshold one-liners**
  (`export CLAUDE_CODE_SCROLL_SPEED=3`, `claude --worktree feature-auth`),
  all within a single chapter — the intended dedup exemption, not a miss.
- **#1 is precise, no over-dedup:** ch-04 kept all 6 *distinct* session
  examples (`continue:true`, `fork_session`, conditional-rewind, multi-
  checkpoint, `SessionStore` protocol, extract-session_id) as real code and
  only cross-referenced the genuinely-identical reused blocks.
- **Mismatch resolved at the source:** the old ch-01 theme-JSON-under-Install
  and ch-05 `AgentDefinition`-under-"Task status updates" are gone — the
  re-outline + #3 gave every block a heading that matches it. #4 fired 0×.
- Cross-refs read cleanly (section-specific explanation kept + a clickable
  link to the canonical block). Prose coherent; lang mix healthy.

### What the validation EXPOSED → motivates #2 + #5
All remaining repetition now traces to ONE cause: **topically-overlapping
outline H2 sections**. The dedup made the redundancy honest (cross-refs)
instead of hidden (copy-paste), revealing redundant *sections*:
- ch-04 "Remote Control" is 80% cross-references — a near-duplicate of
  "Session Management" (both cover persist/resume/InMemory/S3/continue).
- ch-07 (6 real / 10 ref), ch-09 (7/11), ch-13 (6/10) similar.
- ch-02 has four "scroll speed" subtopics across four sections (the 5%
  one-liner residual = the same overlap below the dedup threshold).
Clean chapters with distinct content (ch-02 core, ch-06 IDEs, ch-10) have
≈0 cross-refs. So #2 (section-scope disjointness) + #5 are the next lever.

### Remaining plan
Per [[project_planner_fastmcp_validation_2026_05_23]] — validate on ≥2
corpora before trusting, to avoid over-fitting to claude-code:

1. Re-run Planner+Synth on **claude-code** (config-heavy) AND a second corpus
   re-synthesized with the new code — **browser-use** (python-heavy) or
   **langchain** (mixed). browser-use's current 2 chapters predate the fixes,
   so re-synth it fresh for a fair before/after.
2. Re-run the §2 analysis. **Acceptance:**
   - cross-section dup ratio **< 10–15%** of distinct bodies (from ~45%),
   - cross-chapter dup stays **0**,
   - mismatch **< 1%**,
   - language mix + prose quality unchanged (no regression),
   - both corpora pass — if only claude-code improves, it's over-fit.
3. Spot-read ch-05 (SDK) + ch-01 (install) on claude-code and the 2 worst
   chapters on the second corpus to confirm the repetition is gone and
   sections read as distinct.

## 7. Do NOT

- Don't tune thresholds to claude-code alone (the cross-corpus parity is the
  whole point — fix the mechanism, not the corpus).
- Don't drop pod-level / cgroup… (n/a) — don't drop the per-section source
  citations; they're a strength.
- Don't raise the CoCoA pass fraction blindly to force mismatches down; fix
  the routing/dedup so there's nothing to reject.

## Links
- [[project_synth_dedup_pass_2026_05_28]] — U2–U7 (cross-chapter dedup, the
  part that worked).
- [[project_synth_sota_2026_05_24]] / [[project_code_first_sota_2026_05_24]] —
  SAWC + code-first design context.
- [[project_4front_roadmap_2026_05_25]] — outline/clustering roadmap (section
  orthogonality belongs here).
