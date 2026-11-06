# Book Distiller Agentic Ingestion Assessment — workflow 1.10 run, 2026-11-06

Latest run for *The Art of Cyberwarfare*, finished 18:08 UTC, the first on the workflow 1.10 changes from the [workflow 1.8 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.8.md) (1.9 and 1.10 shipped together). Read-only evidence from the live API (LLM counters, all 275 per-page quality records, all 275 final pages) and the MinIO `quality-report.json` and `patch-proposals`. Every auto-applied model repair was checked with a character diff. The fixes below are workflow **1.11**: offline-tested and statically replayed against this run, **not deployed and not measured by a new run**.

## Verdict

1.10 worked as designed: **22 review pages, down from 32** (the 1.9 + 1.10 replay predicted 22), **no wrong auto-applied repair**, no page-level error, no unverified page, and every fix the earlier assessments called for fired (soft-hyphen pages 37 and 127, page 41, the `C&amp;C` entity pages, the "typical for" pages, page 219's repeated phrase repaired at both places, page 251's URL escapes, page 240's `regime -backed`). What is left is mostly not fixable by a rule: the gate rightly refused four wrong suggestions, six findings are the auditor's own noise, and eight are judgement calls. 1.11 removes the noise that can be recognised without guessing (**22 → 19 review pages** statically) and closes one hole the diff check found: a soft hyphen turned into a visible hyphen on page 37.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`) |
| Docling extraction | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `b571712f8f094422a066e3933e8bd34a`, workflow `1.10` |
| Status | `done`, `complete_with_review`, no run error |
| Time | 2026-11-06 17:52:39–18:08:48 UTC (16m 09s) |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 22 pages · 26 unresolved issues · 0 unverified · 0 repair errors · metadata accepted |

## Comparison with workflow 1.8

| Metric | 1.8 | 1.10 | Change |
| --- | ---: | ---: | ---: |
| LLM calls | 426 | 589 | +38% |
| Input / output tokens | 1,011,404 / 82,152 | 1,580,632 / 137,018 | +56% / +67% |
| Wall time | 18m 21s | 16m 09s | −12% |
| Review pages | 32 | 22 | −10 |
| Unresolved issue instances | 35 | 26 | −9 |
| Auto-applied issues | 76 | 114 | 99 model, 15 mechanical |
| `structure` findings kept | 10 | 5 | −5 |
| Unverified pages | 0 | 0 | |
| Page-level repair errors | 2 | 0 | |
| Provider-level LLM errors | 0 | 0 | |

Calls and tokens rose because more issues now reach a specialist (107 text-repair calls, 16 structure-repair calls) and because the pool differs: 18 auditor models answered 464 calls (the busiest, `poolside/laguna-xs-2.1`, took 136), against 11 models in 1.8. The pool changes every run, so every count here moves with it.

## What was verified

99 model repairs were auto-applied (89 `split_word`, 7 `text_corruption`, 3 `fused_marker`). Character diff of every one:

| Kind | Repairs | Verdict |
| --- | ---: | --- |
| Spaces only (stray space in a word, a URL, a heading) | 81 | all correct |
| Plain-prose hyphen inserted (`spearphishing` → `spear-phishing`, `Asiabased` → `Asia-based`, `commandand-control` → `command-and-control`, …) | 15 | all correct |
| Dangling hyphen joined (`spe- cific` → `specific`) | 1 | correct |
| Soft hyphen replaced by a visible hyphen (`cyber­ attacks` → `cyber-attacks`, page 37) | 1 | defensible but not provable — **fixed in 1.11** |
| `\_` escape dropped in a reference URL (`country\_profiles`, page 242) | 1 | correct, outside code |

No letter or digit changed, no URL got a space, and the seven stray-space URL repairs on page 250 and `regime -backed` on page 240 are exactly the class 1.10 added. The soft-hyphen case: the book spells the word `cyberattack` 57 times, `cyber-attack` 12 and `cyber attack` 10, so the output is not wrong, but a soft hyphen only marks where a line may break; replacing it with a visible hyphen claims a compound the page never showed.

## What the 26 unresolved issues are

| Class | Issues | Pages | Cause |
| --- | ---: | --- | --- |
| Auditor noise (no defect in the evidence) | 6 | 17, 59, 166, 184, 187, 250 | A footnote number read as a stray period (17); a split word with no break in the excerpt (59); three "text and an image" structure comments (166, 184, 187); a sentence-final period on a URL (250) |
| Real defect, the model's fix was wrong and the gate refused it | 4 | 15, 155, 173, 259 | `ACKNOWL EDGMENT S` → `ACKNOWLEDGEMENTS` adds a letter; `## 6` → `## Chapter 6` invents text; `nationstate` → `Nation-state` capitalises; `VirusTotal -Intelligence` drops a hyphen |
| Real defect, no suggestion made | 7 | 163, 194, 240 (2), 247, 250 (2) | Footnote and folio numbers fused into prose; stray spaces inside reference URLs (`edu/ ~jain`, `cfr.org/ cyber-operations`, `business insider.com`) where the auditor's description names a word the excerpt does not contain and the specialist returned nothing |
| Judgement call | 8 | 80, 134, 176, 189 (2), 242, 265, 275 | Em dash vs hyphen, a comma between fused words, a stray numeral, OCR digits in a code block and a URL, a malformed link, an index table, bullets with a repeated tail |
| Unclear | 1 | 150 | A paragraph that may simply continue on the next page |

These are my labels from the stored suggestions, so they are provisional. The four wrong suggestions carried confidences of 0.9 to 1.0, which is why the gate rests on what an edit changes, not on how sure the model is.

## Findings

### 1. The "mixed page" comments still reach review

Pages 166, 184 and 187 carry `structure` findings that say a page "contains a mix of text and an image", that the image "is not properly integrated" or "not rendered or described in the markdown". 1.9's self-dismissal screen catches `typical for` and `structure is coherent`, but these descriptions use other words. Across the five stored runs the same commentary reached review on pages 93, 117, 163, 166, 169, 184, 186, 187, 194, 206 and 207 (`structure` or `other`); every description is about how a picture is handled, and pictures are extracted as separate assets.

### 2. A soft hyphen can become a visible hyphen

The spacing gate ignores soft hyphens, so `cyber­ attacks` → `cyber-attacks` passed as "a hyphen added in plain prose".

### 3. A description that names the wrong word hides a real URL defect

Page 247's finding says `denial-ofservice` while its evidence is `https://www.cfr.org/ cyber-operations/denial-service-attacks…`; the specialist saw no such word and returned an empty list. Pages 240 and 250 repeat the pattern. The stray space is real and space-only.

## Fixes applied (workflow 1.11)

1. **Picture-placement commentary is discarded** (`inventory/patterns.py:IMAGE_PLACEMENT_RE`, applied to `structure` and `other` findings in `sanitize_assessment`, counted in the existing "described as normal" warning). Replayed against all five stored runs, it matches only the picture comments above and nothing else.
2. **A soft hyphen never becomes a visible hyphen** (`repair_pages/domain.py:_is_spacing_repair`): `cyber­ attacks` may become `cyberattacks`, not `cyber-attacks`. If the specialist proposes the hyphen, the finding stays in review.
3. **The auditor prompt** says that how a picture sits among the text, or that a page mixes text and an image, is never a finding; **the text-repair prompt** says the excerpt, not the description, shows the defect, and that a stray space in a URL or split word is proposed even when the description names another word. Both are prompt-only and unmeasured.
4. `WORKFLOW_VERSION` → `1.11`.

Replay against this run: pages 166, 184 and 187 clear (**22 → 19 review pages**). Pages 240, 247 and 250 may clear if the prompt change makes the specialist propose the URL spaces; that is not counted.

### Tests

`scripts/server/test_bd_ingestion_full_workflow.py`: the soft-hyphen positive is now the joined word and the hyphen version is a refusal; three picture-placement descriptions are discarded for `structure` and `other`, and the same wording on a `split_word` finding is kept. All BD offline tests pass.

## Not changed

- The 19 remaining pages are judgement calls, wrong suggestions or defects without a suggestion; no deterministic test separates them from real text.
- A rule that turns an empty specialist answer into a fix (for example, always join `/ ` in a URL) would apply edits no model checked; the prompt change is the cautious version.
- `split_word` evidence without whitespace is not rejected: in five runs that shape was a real fused word (`Asiabased`, `spearphishing`) far more often than noise (`lockergoga-1.png.`).
- Page 242 still holds `http:// news.bbc.co.uk…`; its single finding is the malformed link, which stays a human call.

## Next run: what to check

Review pages ≈ 19 or fewer; no `structure` or `other` finding about a picture; page 37 reads `cyberattacks` or stays in review, never `cyber-attacks`; pages 240, 247 and 250 either repaired or still flagged (that tells whether the text-repair prompt change works). Record the model pool next to the counts.
