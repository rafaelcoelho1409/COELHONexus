# DD Synth — section-count overhaul (hollow cross-ref fix)

**Date:** 2026-05-29 (PM)
**Status:** SHIPPED (uncommitted) — needs redeploy + cold CC re-run to validate.
**Supersedes the open items** in DD-SYNTH-SECTION-RECYCLING-2026-05-29 (#2/#5
"pending re-plan" + the deferred "post-digest section-pool Jaccard merge").

## Why

The CC re-run that was meant to validate #2/#5 (outline scope-orthogonality
prompt + digest single-home routing) showed they did NOT reduce
over-sectioning: 96 cross-ref markers (up from 89), 19 hollow sections
(≥50% of code slots are "see other section"), and **12/13 chapters carried a
PERMANENT unresolved cap violation** ("4 H2 sections but adaptive cap is 3").

### Root cause (new — not the recycling memo's analysis)
Three constants contradicted each other, deadlocking the outline's own
section-count enforcement:

| Knob | Value | Effect |
|---|---|---|
| Pydantic `min_length` (`_SECTIONS_MIN`) | **4** | every chapter FORCED to ≥4 sections |
| adaptive cap `max_h2_for_n_sources` | **3** (n//4 for <16-doc chapters) | validator flags 4 > 3 forever |
| hard-trim floor `max(_SECTIONS_MIN, cap)` | **max(4,3)=4** | a 4-section outline is never trimmed |
| prompt target `_TARGET_SECTIONS_HINT` | **8** | LLM told to make ~8 |
| USC rubric | **"6-12"** | picker rewards over-decomposition |

The LLM emitted **4 sections every chapter** (uniform — even a 4-doc chapter).
The validator flagged it; 2 soft repairs were ignored; the hard-trim that
should cut 4→3 never fired because its floor was 4. Net: one guaranteed-
redundant section per chapter → the digest routes overlapping sources to it →
sawc regenerates the same canonical code (and Ship-A bank-padding back-fills
thin sections with chapter-wide code) → render dedup (#1) strips it to a
hollow "Same code as …" cross-reference. ch-13 was the worst: four
differently-titled sections ("Overview" / "Analytics" / "OpenTelemetry
Configuration" / "Cost Tracking") that are all one topic.

## Fixes shipped (all three the user requested)

### #1 — reconcile the cap so the existing hard-trim fires
- `outline/constants.py`: `_SECTIONS_MIN` 4 → **2** (Pydantic floor).
- `outline/node.py`: hard-trim now uses `max_h2_for_n_sources(n)` **directly**
  (dropped the `max(_SECTIONS_MIN, cap)` override). Re-validates post-trim, so
  the cap violation actually clears.

### #2 — re-pick the adaptive formula + harmonize the whole chain
- `outline/constants.py`: adaptive **floor 3 → 2**, **ceiling 12 → 10**
  (divisor stays 4 — it is what makes a 13-doc chapter cap at 3; the divisor
  was never the bug). New cap by source count: 4-11 → 2, 12-15 → 3, 16-19 → 4,
  20+ → 5.. (≤10).
- `outline/node.py` + `outline/service.py`: the prompt **target is now the
  adaptive cap** (not a fixed 8); prompt text reframed (over-sectioning a
  small chapter creates hollow cross-refs; hard range 2-40).
- `outline/service.py`: **USC rubric** rewards count AT/just-under the
  adaptive cap and penalizes over-decomposition (cap threaded through
  `_usc_pick` → `build_usc_vote_prompt`).
- `max_h2_for_n_sources()` is now the SINGLE source of truth for section
  count (Pydantic min, prompt target, USC rubric, hard-trim, validator).
- `OUTLINE_PROMPT_VERSION` → `v4-adaptive-sections-2026-05-29`.

### #3 — post-digest source-pool merge (the definitive overlap signal)
- `digest/service.py::merge_overlapping_sections` (new): after routing,
  folds sections whose **PRIMARY source pools** overlap into one. Pair rule
  (BIG = more primaries, ties → earlier outline order wins): Jaccard ≥ **0.60**,
  OR containment ≥ **0.80** with the smaller bringing < **2** primary sources
  the bigger lacks (not independently defensible). Re-tags losing
  contributions to the winner (dedup within source; union code_refs/key_facts;
  keep stronger relevance) and returns `{loser: winner}`. Catches what the
  heading/embedding proxy can't (ch-13's same-source, differently-titled
  sections). Conservative — render dedup (#1) stays the safety net.
- `digest/types.py`: `ChapterDigest.merged_sections: dict[str,str]`.
- `digest/node.py`: call after `build_per_section_index`, rebuild the index
  over the re-tagged per_source, persist `merged_sections`, surface
  `n_merged_sections` in stats + the `done` SSE.
- `sawc/node.py`: **skip merged sections** in the stage loop — otherwise
  Ship-A bank-padding back-fills the emptied loser with chapter-wide canonical
  code and re-creates the hollow section. (render reads sawc output, so merged
  sections vanish from the book — no render change.)
- `DIGEST_PROMPT_VERSION` → `v3-source-pool-merge-2026-05-29`.

Cache: outline v4 → digest (keys on outline hash + its own version) → sawc
(keys on both) → render (keys on sawc). Full cold recompute on re-run; no
manual cache wipe needed.

## Validation done (logic, local — pydantic 2.13.4)
- `max_h2_for_n_sources`: 4→2, 8→2, 13→3, 16→4, 20→5, 50→10. `_SECTIONS_MIN`=2.
- merge: monolithic (4 sections sharing 2 authorities) → folds to 1; distinct
  primaries → no merge; partial overlap where each brings ≥2 unique → no merge.

## Predicted CC outline caps (from the audited source counts)
ch-01(13)→3, ch-02(11)→2, ch-03(10)→2, ch-04(7)→2, ch-05(17)→**4** (the one
legit-large chapter, unchanged), ch-06(5)→2, ch-07(8)→2, ch-08(7)→2,
ch-09(8)→2, ch-10(6)→2, ch-11(8)→2, ch-12(4)→2, ch-13(6)→2 (likely →1 after
the source-pool merge if it's truly monolithic).

## Acceptance (re-assess after the cold re-run)
- 0 chapters with a cap `final_violation`; section count = adaptive cap.
- cross-ref markers ≪ 96; hollow sections ≪ 19 (target: a handful).
- audit still 13/13, cross-chapter dup 0, mismatch 0.
- WATCH: chapters that merge to a single H2 (acceptable for monolithic
  topics, but if common it signals the planner is making chapters too small).
- Confirm on a 2nd corpus (browser-use) to rule out CC over-fit.
