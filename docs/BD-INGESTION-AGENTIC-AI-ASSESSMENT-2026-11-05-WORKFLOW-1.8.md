# Book Distiller Agentic Ingestion Assessment — workflow 1.8 run, 2026-11-05

Latest run for *The Art of Cyberwarfare*, finished 21:35 UTC, the first on the workflow 1.8 changes from the [workflow 1.7 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.7.md). Read-only evidence from the live API (LLM counters, all 275 per-page quality records) and the MinIO `quality-report.json`. Every auto-applied model repair was checked with a character diff. The fixes below are workflow **1.9**: offline-tested and statically replayed against this run, **not deployed and not measured by a new run**.

## Verdict

1.8 did its job on cost and safety — **426 calls, the fewest yet, no wrong auto-applied repair** — but review pages went **up, 21 → 32**, and two of the 11 extra pages are my regression: the 1.8 gate lets a space-only repair swallow a soft hyphen, and the soft-hyphen finding on that page was then never marked resolved. The rest of the increase is auditor sampling noise (a different model mix reports more layout findings and three entity "findings" it labels "not a finding"). 1.9 fixes the regression and rejects the noise that can be recognised without guessing; **32 → 24 review pages** statically.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`) |
| Docling extraction | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `e76724f4f15e44a98242f5ac89191f4b`, workflow `1.8` |
| Status | `done`, `complete_with_review`, no run error |
| Time | 2026-11-05 21:17:01–21:35:22 UTC (18m 21s) |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 32 pages · 35 unresolved issues · 0 unverified · 2 repair errors · metadata accepted |

## Comparison with workflow 1.7

| Metric | 1.7 | 1.8 | Change |
| --- | ---: | ---: | ---: |
| LLM calls | 550 | 426 | −23% |
| Input / output tokens | 1,401,209 / 101,541 | 1,011,404 / 82,152 | −28% / −19% |
| Wall time | 13m 26s | 18m 21s | +37% |
| Review pages | 21 | 32 | +11 |
| Unresolved issue instances | 23 | 35 | +12 |
| Auto-applied issues | 89 | 76 | 66 model, 10 mechanical |
| `structure` findings kept | 3 | 10 | +7 |
| Unverified pages | 0 | 0 | |
| Page-level repair errors | 1 | 2 | |

Wall time rose while calls fell: the models answering this run were slower, not busier (11 auditor models, 347 calls). The model pool differs between runs, so every count here moves with it; zero provider-level LLM errors.

## What was verified

66 model repairs were auto-applied (63 `split_word`, 3 `text_corruption`). Every one changes only spaces, plus hyphen insertions in plain prose (`spearphishing` → `spear-phishing`, `ninehour` → `nine-hour`) and the dangling-hyphen joins. No letter or digit changed, no URL got a space (the 1.8 URL guard held: page 254's `offensesrelated-2016-election` stayed in review). Three proposals the gate correctly refused: `denial-of-service` → `denialofservice` (page 56, removes real hyphens), `infosec institute` → `infosec.institute` (page 256, a letter-class change) and `Wire shark` → `Wireshark`.

## What the 35 unresolved issues are

| Class | Issues | Cause |
| --- | ---: | --- |
| `soft_hyphen` | 2 | **Regression** (pages 37, 127): the model's `cyber\xad attacks` → `cyberattacks` repair applied, removing the soft hyphen, so the mechanical pass counted none and left the deterministic finding open |
| `other` | 3 | The same `C&amp;C` entity on pages 48, 81, 82; two descriptions say "is not a finding" |
| `toc_noise` | 1 | Page 41: ". . ." inside a quoted sentence ("Egypt and . . ., and also") taken for a contents leader |
| `structure` | 10 | "A mix of text and an image … typical" (93, 207), caption-less layout comments, a heading change (28), a code block (161), an invented `[6]` marker (213) |
| `split_word`, `text_corruption` | 15 | Letter changes the gate refuses, URLs with `\_` escapes, a Sofacy phrase whose repair was rejected as ambiguous (219), a target the page does not contain (247), split-word findings the specialist made no proposal for (24, 33) |
| `table_or_index_layout` | 4 | Index pages 268, 271, 272 and one padded table (211) |

## Findings

### 1. A space-only repair hid a soft-hyphen finding (regression)

1.8 made `cyber\xad attacks` → `cyber-attacks`-style repairs eligible. They run before the mechanical cleanup, so by the time `apply_mechanical_fixes_for_issues` looked, the soft hyphen was already gone and `counts["soft_hyphen"]` was 0; the finding was only resolved when the pass itself removed the character. Two pages were left in review with their text already correct.

### 2. Models file findings they then call normal

Pages 81 and 82: "The page contains an HTML entity that is not a finding." Pages 93 and 207: "a mix of text and an image … typical for a mixed-content page"; "the overall structure is coherent". The prompt tells them these are normal; the weaker models in the pool report them anyway with a self-dismissing sentence. Across the last three runs seven findings would have been caught by that wording alone (`not a finding/defect/problem`, `typical for`, `structure is coherent`), all genuine non-defects.

### 3. An ellipsis in prose passed as a contents leader

The `toc_noise` check required only repeated leader marks. A quoted sentence ending ". . .," met it. A contents row's leaders end the cell or line or lead straight into a page number.

### 4. A repeated phrase made a verified repair "ambiguous"

Page 219 repeats `Sofacy/ Sednit`; the validator rejects any target that occurs twice, so a space-only fix that is identical at both places was lost.

## Fixes applied (workflow 1.9)

1. **Soft-hyphen and control-character findings resolve against the cleaned text**, whoever removed the character (`repair_pages/domain.py:apply_mechanical_fixes_for_issues`).
2. **Self-dismissing findings are dropped** (`inventory/patterns.py:SELF_DISMISSED_RE`, applied in `sanitize_assessment`, reported as a warning).
3. **`toc_noise` evidence must look like a contents row** (`TOC_LEADER_ROW_RE`: leaders at the end of a cell or line, or before a page reference); `other` findings whose evidence holds an HTML entity are rejected.
4. **A verified space-only repair may name a phrase that occurs more than once.** `validate_proposals` accepts the ambiguous target only when the repair is space-only at every occurrence; `apply_proposals` now works on one span per occurrence and applies all of them or none (still blocked inside a URL, path or address).
5. `WORKFLOW_VERSION` → `1.9`.

Replay against this run: soft-hyphen pages 37 and 127, page 41 (leaders), pages 48, 81, 82 (entity) and pages 93, 207 (self-dismissed) clear: **32 → 24 review pages**. Page 219 should also clear next run (its repair is now accepted); that is not counted.

### Tests

`scripts/server/test_bd_ingestion_full_workflow.py` adds: a soft-hyphen finding resolved after a space-only repair, a repeated phrase repaired at both places (and refused inside a URL), the ambiguity test now using a letter-changing proposal, entity, prose-ellipsis and self-dismissal evidence rejected while a real contents row passes. All BD offline tests pass.

## Not changed

- The remaining pages are judgement calls or letter changes (index pages, `\_` URL escapes, heading level edits, code blocks); no deterministic test separates them from real defects.
- Page 247's target is not on the page at all; nothing to repair.
- The model pool varies between runs; comparing review-page counts across runs without comparing pools overstates what the code changed. The next assessment should record the pool alongside the counts.

## Next run: what to check

Review pages ≈ 24 or fewer, no `soft_hyphen` finding left in review after a model repair, no entity or "typical for" finding, page 219's phrase repaired in both places, and page 41 absent from the review list.
