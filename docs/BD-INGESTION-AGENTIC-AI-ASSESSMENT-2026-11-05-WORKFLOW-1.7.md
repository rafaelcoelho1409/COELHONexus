# Book Distiller Agentic Ingestion Assessment — workflow 1.7 run, 2026-11-05

> Historical report: the latest completed run is workflow 1.8; see the [workflow 1.8 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.8.md). Its fixes shipped as 1.8; the soft-hyphen regression they introduced is described and fixed there.

Latest run for *The Art of Cyberwarfare*, finished 20:29 UTC, the first on the workflow 1.7 changes from the [workflow 1.6 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.6.md). Read-only evidence from the live `coelhonexus-dev` API (LLM counters, all 275 per-page quality records) and the raw pages for the cases below. All 71 auto-applied model repairs were checked with a character diff, and those new since the previous run were read by hand. The fixes below are workflow **1.8**: offline-tested and statically replayed against this run, **not deployed and not measured by a new run**.

## Verdict

1.7 did what the replay predicted: **36 → 21 review pages, again exactly the estimate**, with no unverified page for the first time and one page-level error (was five). Auto-applied issues rose from 77 to 89. One auto-applied repair is wrong: page 254 had a space inserted into a URL path. The gate allowed it because adding a space is "space-only"; 1.8 closes that hole and the other remaining gaps. What is left (17 pages after 1.8) is judgement calls and weak-model noise, which no further rule removes safely.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`) |
| Docling extraction | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `f7c427046579468ab0fcfb2bb7b95267`, workflow `1.7` |
| Status | `done`, `complete_with_review`, no run error |
| Time | 2026-11-05 20:15:50–20:29:16 UTC (13m 26s) |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 21 pages · 23 unresolved issues · 0 unverified · 1 repair error (page 24, schema mismatch) |

## Comparison with workflow 1.6

| Metric | 1.6 | 1.7 | Change |
| --- | ---: | ---: | ---: |
| LLM calls | 579 | 550 | −5% |
| Input / output tokens | 1,452,222 / 103,367 | 1,401,209 / 101,541 | −4% / −2% |
| Wall time | 11m 52s | 13m 26s | +13% |
| Review pages | 36 | 21 | −42% |
| Unresolved issue instances | 44 | 23 | −48% |
| Auto-applied issues | 77 | 89 | +12 (71 model, 18 mechanical) |
| `structure` findings kept | 17 | 3 | −82% |
| Structure-specialist calls | 35 | 9 | −74% |
| Unverified pages | 2 | 0 | |
| Page-level repair errors | 5 | 1 | −80% |
| Pages with auditor warnings | 39 | 42 | flat |

Different model pools ran each time (the auditor spread over ten models), so call counts and time are not attributable to code alone; wall time rose although calls fell. Zero provider-level LLM errors.

## What was verified

71 model repairs were auto-applied (64 `split_word`, 6 `text_corruption`, 1 `fused_marker`). Every one changes only spaces, plus 7 hyphen insertions in plain prose (`nationstate` → `nation-state`, `spearphishing` → `spear-phishing`), four dangling-hyphen joins (`partici- pants` → `participants`) and the Japanese `ター ゲット` → `ターゲット`. No letter or digit changed. **One is wrong**: page 254 `offensesrelated-2016-election` → `offenses related-2016-election` puts a space into a URL path. The same text was proposed as `offenses-related-2016-election` (correct, hyphen) in the previous run and went to review; this run the model chose a space and the gate accepted it.

The gate also earned its keep the other way: on page 15 the model changed `ACKNOWL EDGMENT S` to `ACKNOWLEDGEMENTS` (an added `E`, the previous run had `ACKNOWLEDGMENTS`), which the letter rule refused and sent to review.

## What the 23 unresolved issues are

| Class | Issues | Cause |
| --- | ---: | --- |
| `table_or_index_layout` | 6 | Three are a figure caption described as a table (109, 150, 185); 211 and 217 are rewrites (a bullet list turned into a table at 0.9); 222 is an IP list rewrite |
| `split_word` | 11 | Letter changes the gate refuses (`Fax..:`, `Wire shark` → `Wireshark`, `\_` escapes), an IP address split by a space that was blocked by an overlapping review-only proposal (222), `cyber\xad attacks` (37), and proposals the specialist never made (24, 241, 258, 273) |
| `structure`, `text_corruption`, `other`, `near_empty` | 6 | Title page called "near empty" (5), inline footnote numbers (48, 104), two layout comments (169, 187), an escaped-underscore URL (241) |

## Findings

### 1. A space insertion inside a URL was accepted

The gate verifies that only whitespace changed, not that the whitespace belongs there. Removing a space from a URL is always safe; adding one never is. Page 254 is one wrong repair in 71, in a reference list a reader would trust.

### 2. A review-only proposal blocked a verified repair

Page 222: a specialist rewrote a whole IP list at 0.9 confidence (review-only) and the fix `173.194.121 .36` → `173.194.121.36`, which is space-only, sat inside that span. The overlap rule treated any two overlapping proposals as colliding and held both back, though the rewrite is never applied.

### 3. A soft hyphen plus a stray space was still outside the prose class

`cyber\xad attacks` → `cyber-attacks` (page 37) is the right repair, but the soft hyphen kept both strings out of the plain-prose hyphen rule.

### 4. Captions and title pages still produce layout and sparseness findings

Pages 109, 150 and 185: `table_or_index_layout` whose only evidence is a `Figure N:` caption. Page 5: a model calls a title page "near empty" although the deterministic inventory already measures sparseness by character count and did not fire.

### 5. What the rules cannot remove

Of the remaining pages the proposals either change letters (correctly refused), rewrite structure (judgement), or are missing because the specialist returned nothing or broke its schema. Page 273's `denial-ofservice` finding cites an unrelated index row as evidence; without a dictionary no check separates it from a real split.

## Fixes applied (workflow 1.8)

1. **No inserted space inside a path or address.** `_is_spacing_repair` now takes the page and the span. If the repair inserts whitespace and the unbroken text around the span contains `/`, `\` or `@`, it stays review-only. Removing a space there is still applied.
2. **Overlap only among repairs that could apply.** `apply_proposals` first picks the eligible proposals, then blocks only overlaps between two eligible ones; a review-only proposal no longer holds back a verified repair inside its span. Two competing verified repairs still both wait.
3. **Soft hyphen is part of the plain-prose class** for the hyphen rule.
4. **Evidence screen:** `table_or_index_layout` with caption-only evidence is rejected like `structure`; auditor `near_empty` is rejected (the inventory owns sparseness).
5. `WORKFLOW_VERSION` → `1.8`.

Replay against this run: pages 5, 109, 150, 185 clear (findings dropped), page 37 and the page-222 IP fix become auto-applied, and page 254's URL edit goes back to review: **21 → 17 review pages** (page 222 stays for its separate table rewrite; page 254 joins). The count rises by one on purpose.

### Tests

`scripts/server/test_bd_ingestion_full_workflow.py` adds: a space refused inside a URL path and inside an address, the soft-hyphen-to-hyphen join applied, a review-only rewrite not blocking an inner IP fix while two competing fixes both wait, and caption-only table evidence and model `near_empty` rejected. All BD offline tests pass.

## Not changed

- The 17 remaining pages need a human or a better model pool: letter changes, structural rewrites, footnote numbers fused into prose, escaped underscores in URLs. Auto-applying any of them would trade a review page for unverifiable edits.
- Page 273's mismatched-evidence finding and the `Figure 7-4 shows …` prose finding (page 187) have no safe deterministic test.
- Two parallel runs of the same book are not identical; the pool is the largest remaining source of variance.

## Next run: what to check

Review pages ≈ 17, page 254's URL untouched or hyphenated, page 222's IP address joined, page 37 `cyber-attacks`, no auto-applied repair that inserts whitespace into a URL, and a sample of the hyphen insertions against the source.
