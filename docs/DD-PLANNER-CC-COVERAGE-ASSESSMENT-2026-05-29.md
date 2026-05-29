# DD Planner — Claude Code coverage assessment (LLM-first planner)

**Date:** 2026-05-29
**Status:** ASSESSED. Structure is excellent; coverage is the weak spot.
Two fixes recommended (NOT shipped — documented for the next planner pass).
**Scope:** audit of the LLM-first Planner output for the `claude-code` corpus
(doc_distill → chapter_propose → chapter_assign → chapter_select →
plan-latest), done while the Synth re-run was in flight. Planner artifacts
are stable (only synth/ regenerates), so this is safe to read mid-run.

---

## 1. Verdict

The Planner's **structure** is in great shape — a large improvement over the
old classical under-clustering (CC 130→2ch with 91% in one cluster). No revert
concern. But it **silently drops ~18 genuine content docs** from the book via
too-conservative assignment + silent distillation failures.

## 2. What's good

- **13 clean, SCOPE-DISTINCT chapters.** No chapter pair exceeds 18% lexical
  overlap on title+description → the chapter decomposition has NO overlap
  problem. (The hollow-section overlap seen in the rendered chapters is a
  SYNTH-OUTLINE issue — H2 sections within a chapter — addressed by
  DD-SYNTH-SECTION-RECYCLING #2/#5, NOT a planner defect.)
- **Balanced + well-ordered.** 110 docs / 13 chapters, avg 8.5 (range 4–17),
  sensible pedagogical order: install → commands → permissions → session →
  SDK → IDEs → plugins → hooks → MCP → agents → cloud → CI/CD → monitoring.
- **Clean single-membership assignment.** Each doc is scored per-chapter
  (e.g. 0.9 to its home, 0.3 elsewhere) and placed in exactly ONE chapter →
  distinct_docs (110) == sum_sources (110), zero cross-chapter duplication.
- **Efficient propose/select.** chapter_propose: optimal-stopping fired
  (1/3 samples, 15 proposals). chapter_select: greedy coverage took 13.

## 3. What's wrong — coverage

**140 ingested → 110 in the book → 30 dropped (21%).**

- **~12 of the 30 are legitimately non-content** (correct drops): `changelog`,
  7× `week-NN-...` release-notes, `legal-and-compliance`, `communications-kit`,
  `what-s-new`, `channels-reference`.
- **~18 are GENUINE content**, several clearly mis-dropped despite an obvious
  home chapter:
  - `track-cost-and-usage` → obviously ch-13 (Monitoring & Cost Management)
  - `intercept-and-control-agent-behavior-with-hooks` → obviously ch-08 (Hooks)
  - `best-practices-for-claude-code`, `claude-code-settings`, `tools-reference`,
    `give-claude-custom-tools`, `create-custom-subagents`, `extend-with-skills`,
    `how-the-agent-loop-works`, `use-claude-code-features-in-the-sdk`,
    `streaming-input`, `troubleshoot-installation-and-login`, `troubleshooting`,
    `customize-your-status-line`, `claude-code-in-slack`, `chrome-beta`.
- **Dropped chapter:** `Skills and Custom Commands` was proposal idx 13 but
  chapter_select didn't pick it (greedy coverage), so its docs (`extend-with-
  skills`, `create-custom-subagents`) fell through to unassigned → that content
  is missing entirely.
- **17 silent distillation failures (13%)** — `cli-reference`, `quickstart`,
  several SDK refs, `configure-permissions`, hooks — with **NO error reason
  recorded** (failures list is just source keys). 16/17 still reached the plan
  via fallback (assignment tolerates a missing distillate), so they're mostly
  NOT lost — BUT the missing distillate starves the assigner of signal: the one
  not recovered (`...with-hooks`) was dropped despite ch-08 Hooks existing.

Pipeline accounting: doc_distill n_files=129 (11 non-content pre-filtered),
n_distilled=112, n_failed=17. chapter_assign n_docs=129, n_assigned=110,
n_failed=19. plan stats report n_unassigned=0 / n_dropped=0 — a DIFFERENT
accounting (post-select chapter orphans) that masks the 30 real drops; the
honest number is 110/140 ingested in the book.

## 4. Root cause + recommended fixes (NOT shipped)

1. **Silent distill failures** — likely oversized docs (cli-reference is huge)
   and/or rotator 429s; no error is captured. FIX: log the per-doc failure
   reason + add a retry / oversize-chunk path so core docs get a distillate.
   Restoring distillates gives the assigner real signal for the docs it
   currently drops.
2. **Assignment is too conservative** — it drops docs that fit an obvious
   chapter (cost→ch-13, hooks→ch-08). FIX: add a RESCUE pass — any unassigned
   doc whose best-chapter confidence is above a low floor goes to that chapter
   instead of being dropped. Consider also not pruning a proposed chapter
   (Skills) whose member docs would otherwise become orphans.

## 5. Bottom line

Planner structure: PASS, no revert. Coverage: ~18 real docs (best-practices,
settings, skills, subagents, tools-reference, custom-tools, cost-tracking,
agent-loop, hooks deep-dive, troubleshooting) silently absent from the book.
Fixing distillation reliability + an assignment rescue pass would close most
of the gap. Independent of the synth re-run / the DD-SYNTH-SECTION-RECYCLING
work.

## Links
- [[project_planner_llm_first_2026_05_27]] — the LLM-first planner this audits.
- [[project_synth_section_recycling_2026_05_29]] — the synth-outline overlap
  fixes (#2/#5); chapter-level overlap here is CLEAN, do not conflate.
