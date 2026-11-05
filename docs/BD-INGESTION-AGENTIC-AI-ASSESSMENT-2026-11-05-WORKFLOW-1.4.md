# Book Distiller Agentic Ingestion Assessment — workflow 1.4 run, 2026-11-05

> Historical report: the latest completed run is workflow 1.5; see the [workflow 1.5 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.5.md).

Latest completed run found for *The Art of Cyberwarfare*. Read-only run evidence came from the live `coelhonexus-dev` API, its MinIO manifest and quality report, all 275 per-page quality artifacts, and focused page text. The follow-up code changes described below are local workflow 1.5 changes and have not been deployed or measured in a new run.

## Verdict

Workflow 1.4 improved repair reliability and metadata handling while using fewer LLM resources than workflow 1.3. It completed in 10 minutes with 513 calls, down from 580 calls and 16 minutes. The metadata title, subtitle, and author were accepted at confidence 1.0; structured-output audit fallbacks dropped from two to zero; and repair errors dropped from 29 records to 12.

Content review remains the limiting factor. The run has 127 review pages and 150 unresolved issue instances. That is eight more review pages than workflow 1.3, despite eight fewer unresolved issues. Fifty `toc_noise` findings dominate the issue report; 46 have no dotted leader in any evidence excerpt. Five of the six `control_characters` findings also have no actual control character in their evidence. These are evidence-code mismatches, not supported defects.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`) |
| Docling extraction | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `8675dbc8812e4df3840ab7e54da1d280`, workflow `1.4` |
| Manifest schema | `1.2` |
| Status | `done`, `complete_with_review`, no run-level error |
| Time | 2026-11-05 17:24:16–17:34:16 UTC (10m 01s) |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 127 pages · 150 unresolved issue instances · page 270 unverified |

The 127 review pages include 126 pages with unresolved issue IDs and 12 pages with repair errors; these reason counts are page counts and overlap. `unresolved_issue_count` is the count of issue instances. Page 270 exceeded the 30,000-character audit and repair limits, so it has no LLM audit, no specialist repair, and remains unverified for manual review.

## Comparison with workflow 1.3

| Metric | Workflow 1.3 | Workflow 1.4 | Change |
| --- | ---: | ---: | ---: |
| LLM calls | 580 | 513 | −11.6% |
| Input tokens | 1,324,743 | 1,132,182 | −14.5% |
| Output tokens | 137,417 | 99,484 | −27.6% |
| Wall time | 16m 07s | 10m 01s | −37.8% |
| Structured-output audit fallbacks | 2 | 0 | −100% |
| Review pages | 119 | 127 | +8 |
| Unresolved issue instances | 158 | 150 | −8 |
| Repair-error records | 29 | 12 | −17 |

The runs used different model pools, so lower usage and duration cannot be attributed solely to code changes. The repair-error reduction and successful metadata artifact are directly visible in persisted run data.

## Usage and disposition

| Node | Calls | Input tokens | Output tokens | LLM counter errors |
| --- | ---: | ---: | ---: | ---: |
| Page auditor | 339 | 774,710 | 57,346 | 0 |
| Text repair | 77 | 166,281 | 18,267 | 0 |
| Structure repair | 93 | 182,444 | 23,271 | 0 |
| Metadata normalizer | 4 | 8,747 | 600 | 0 |
| **Total** | **513** | **1,132,182** | **99,484** | **0** |

The zero LLM counter errors do not mean there were no page-level repair errors. Eight specialist responses failed schema validation, one text specialist reached its model-call limit, two pages had overlapping proposals, and page 270 exceeded the repair size limit. These application-level errors are persisted in page artifacts but are not counted as provider call errors.

The quality report records 171 findings: 50 `toc_noise`, 43 `split_word`, 26 `other`, 21 `table_or_index_layout`, 15 `text_corruption`, 6 `control_characters`, 5 `structure`, 2 each `near_empty` and `soft_hyphen`, and 1 `fused_marker`. It records 75 proposals, 21 automatically resolved issue IDs, and 150 issue IDs requiring review. Mechanical cleanup changed 30 table blocks and removed five control characters and two soft hyphens.

Metadata is no longer a blocker. The saved proposal and accepted fields contain title “The Art of Cyberwarfare”, subtitle “An Investigator's Guide to Espionage, Ransomware, and Organized Cybercrime”, and author Jon DiMaggio. Confidence is 1.0 and `review_required` is false.

## Findings

### 1. Most `toc_noise` findings lack evidence for that code

An inspection of all persisted page-quality artifacts found that 46 of the 50 `toc_noise` findings had no repeated dotted leader or ellipsis in any cited evidence excerpt. Examples include ordinary prose, figure captions, isolated page numbers, and bibliography entries. Page 30's prose finding even proposed appending page number `19` to a sentence; the source already contained that page number at the end of the preceding paragraph.

This is the largest avoidable source of review noise. The auditor prompt already limits `toc_noise` to visibly irregular dotted-leader contents rows, but the persisted results show that prompt wording alone did not enforce the boundary.

### 2. Five `control_characters` findings are also unsupported

Five of the six findings did not cite a non-whitespace Unicode control character. Examples were the HTML entity `C&amp;C`, ordinary spaces inside URLs, and literal Markdown escapes such as `\\_`. Only the finding on page 3 was backed by actual control characters, which the deterministic cleanup removed.

### 3. Overlapping patches discard otherwise reviewable proposals

Pages 241 and 246 each have a `patch proposals contain overlapping source spans` error. In both cases a broad citation rewrite overlapped a narrower URL correction. The current application rejects the entire batch and records every issue for review, even though these model proposals are never auto-applied under the existing policy. The proposals themselves are exact-source and remain useful to a human reviewer.

### 4. Other review items need a different treatment

- Eight repair assignments ended in specialist-schema mismatches (six structure, two text); persisted artifacts do not include raw model responses, so the specific mismatches cannot be safely repaired from this run alone.
- Page 49 reached the text-repair model-call limit.
- Page 270 remains unverified because it exceeds the configured 30,000-character limits.
- Page 162's high-confidence text-corruption proposal targets a MIME header but its rationale says the header is valid and should not change. This supports keeping the conservative auto-apply gate; a confidence score alone is not enough to trust a patch.
- The workflow `plan_repairs` node made no LLM calls, as expected. It deterministically routes issues to specialists, and the architecture explicitly avoids a planner agent. No change is needed there.

## Fixes applied for workflow 1.5

1. Auditor evidence is now checked against the reported code. A `toc_noise` excerpt must contain a visible dotted leader or ellipsis; `control_characters` must contain an actual non-whitespace Unicode control character; and `soft_hyphen` must contain U+00AD. Unsupported excerpts are discarded with an audit warning, and a finding with no supported evidence is dropped. Deterministic inventory findings remain independently validated.
2. The auditor system prompt and skill now state these evidence requirements and explicitly exclude ordinary prose, captions, isolated page numbers, URL/reference whitespace, literal escape sequences, and HTML entities from those codes.
3. Overlapping patch proposals are retained as review-only items. Every proposal in an overlapping span group is blocked from automatic application, while unrelated non-overlapping proposals continue through normal policy. The overlap no longer invalidates the page's entire proposal batch or creates a repair error.
4. The workflow version is bumped to `1.5`; manifest schema remains `1.2`.

The persisted evidence suggests that the new code-specific checks would filter 46 unsupported `toc_noise` findings and five unsupported `control_characters` findings from a comparable run. This is a static replay against cited excerpts, not a workflow rerun; the effect on final review-page count remains unmeasured. No deployment or new ingestion run was started.

## Assessment basis

Read the live API status and LLM counters, the MinIO manifest and quality report, all 275 per-page quality artifacts, metadata artifact, and focused content/proposal records on pages 30, 55, 162, 241, 246, and 270. `plan_repairs` behavior was checked against the graph implementation and architecture notes. The source document was not proofread end to end. No tests were run.
