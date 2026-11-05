# Book Distiller Agentic Ingestion Assessment — workflow 1.3 run, 2026-11-05

## Verdict

Workflow 1.3 is a substantial efficiency and audit-reliability improvement over workflow 1.2: it used **580 LLM calls, 1.32 million input tokens, and 16.1 minutes**, compared with 1,650 calls, 11.35 million input tokens, and 75.3 minutes. Only two pages fell back after an invalid audit response; one more page exceeded the audit size limit.

Content quality still needs substantial review. The run has **119 pages requiring review**, **158 unresolved issue instances**, 28 pages with repair errors, and metadata that remains marked for review. Deterministic cleanup changed 30 tables and removed five control characters and two soft hyphens. The model's proposed text corrections remain review-only under the current safety policy.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`) |
| Docling extraction | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `39916bfcc632435f89103882d6ee84ab`, workflow `1.3` |
| Manifest schema | `1.2` |
| Run status | `done`, `complete_with_review`, no run-level error |
| Time | 2026-11-05 16:22:09–16:38:16 UTC (16m 07s) |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 119 pages; unverified audits on pages 11, 208, and 270 |

## Comparison with workflow 1.2

| Metric | Workflow 1.2 | Workflow 1.3 | Change |
| --- | ---: | ---: | ---: |
| LLM calls | 1,650 | 580 | −64.8% |
| Input tokens | 11,348,097 | 1,324,743 | −88.3% |
| Output tokens | 402,934 | 137,417 | −65.9% |
| Wall time | 75.3 min | 16.1 min | −78.6% |
| Structured-output audit fallbacks | 111 | 2 | −98.2% |
| Pages requiring review | 171 | 119 | −30.4% |

The review totals are not a pure quality comparison: workflow 1.2 had 111 failed or degraded audits, while workflow 1.3 has only two structured-output fallbacks and one size-limited audit. Workflow 1.3 therefore surfaced more pages with supported findings. Its unresolved issue count (158) is higher than the 64 reported for workflow 1.2 and remains a meaningful review backlog.

## Usage and quality detail

| Node | Calls | Input tokens | Output tokens | Errors |
| --- | ---: | ---: | ---: | ---: |
| Page auditor | 354 | 817,868 | 65,027 | 0 |
| Text repair | 103 | 247,718 | 39,022 | 0 |
| Structure repair | 122 | 257,503 | 33,064 | 1 |
| Metadata normalizer | 1 | 1,654 | 304 | 0 |
| **Total** | **580** | **1,324,743** | **137,417** | **1** |

The recorded provider error was an `OpenAIAPIError` during structure repair: the provider reported that it was repeating the same output chunk. Bounded model calls prevented this from turning into the retry and context-growth pattern seen in workflow 1.2.

The quality report contains 169 issue instances: 46 `split_word`, 34 `toc_noise`, 27 `structure`, 20 `text_corruption`, 16 `table_or_index_layout`, 11 `other`, 7 `control_characters`, 5 `metadata`, 2 `soft_hyphen`, and 1 `fused_marker`. Eleven issue IDs were deterministically resolved: 1 control-character issue, 2 soft-hyphen issues, 7 table/index issues, and 1 TOC issue. The report records 30 table blocks cleaned, 5 control characters removed, and 2 soft hyphens removed; operation counts and resolved issue counts measure different things.

There were 60 model proposals. The system did not automatically apply model-generated text or structure changes. That is consistent with the current safety gate, which only auto-applies high-confidence mechanical edits. For example, page 17 proposes changing `I NTRODUC T I ON` to `INTRODUCTION` at 0.95 confidence and page 21 proposes joining `L ANDSCAPE` at 1.0; both remain unchanged for human review.

## Main findings

1. **The repair path still loses otherwise usable batches.** There were 29 repair-error records across 28 pages. Eleven were caused by a specialist returning multiple proposals for the same issue ID; the validator rejected the entire response, including other valid proposals in that response. Seven more were schema mismatches, five reached the four-call cap, two returned no JSON object, two proposed targets that were absent or ambiguous, one hit the provider repeating-chunk error, and one page exceeded the repair size limit.
2. **TOC cleanup missed page references in their own table cell.** Page 13 still has a row like `Attribution Mistakes . . . | . . 126`. The existing cleanup strips leaders from title cells when the last cell is already a bare page reference, but it does not first remove leaders that precede the page number in that final cell. This issue was correctly left unresolved.
3. **Metadata fell back despite a usable title.** The saved artifact uses the page-heading fallback title “The Art of Cyberwarfare”, but has no authors and remains `review_required`. The normalizer returned `structured_response: null`; the code passed `None` to Pydantic instead of trying JSON from the assistant message. The persisted error is that `None` is not a `BookMetadataProposal`.
4. **Page 270 is too large for the current agent limits.** Its index exceeds the 30,000-character audit and repair limits. It received deterministic table cleanup, but no LLM audit or specialist repair; it is correctly listed as unverified and requiring review.
5. **Page-level warning accounting is informative.** The report records 52 warning instances across 36 pages, including two audit fallbacks and evidence or page-kind warnings. Those warnings do not inflate the known unresolved-issue count.

Pages 3, 9, 11, 13, 17, 21, 97, and 270 were inspected against their source and normalized artifacts. Page 3's five control characters were removed; the contents tables were compacted; and pages 17 and 21 retain the proposed heading repairs for review. This is a targeted inspection, not a human proofreading pass over all pages.

## `plan_repairs` makes no LLM calls by design

This is expected. [`plan_repairs/domain.py`](../apps/server/ai/bd/nodes/ingestion/plan_repairs/domain.py) deterministically groups audit issue codes into text- and structure-specialist work items. The node does not construct or invoke an agent. The architecture explicitly chose deterministic routing instead of a planner agent. Its graph status should be `done`; a missing LLM counter entry is normal and requires no code change.

## Fixes applied after this run

These fixes set the workflow version to **1.4**; the persisted manifest schema remained **1.2**.

1. Duplicate proposals for an issue no longer invalidate the entire specialist batch. The first proposal that passes the page, evidence, and assignment checks is retained; later proposals for that issue are ignored. The existing safety gate still controls whether a retained proposal can be applied automatically.
2. Table cleanup now recognizes a dot leader before a page number in the final table cell. TOC evidence matching also checks the individual cells of a cited Markdown row, so a repaired row can be marked resolved.
3. Metadata parsing now recovers a `BookMetadataProposal` from a JSON object in the last assistant message when `structured_response` is absent or null. The existing evidence checks and confidence gate still run before metadata is accepted.

At the time of this report, these changes had not yet been deployed or run against the book. They were subsequently exercised in workflow 1.4 on the same extraction; see the [workflow 1.4 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.4.md). The metadata proposal was accepted, structured-output audit fallbacks fell from two to zero, and repair errors fell from 29 records to 12. Review pages rose from 119 to 127, so the run improved repair reliability without reducing the number of pages needing human review.

## Assessment basis

Read the live `coelhonexus-dev` API status and LLM counters, the MinIO run manifest and quality report, all 275 page-quality artifacts, the metadata artifact, and focused source/normalized page diffs. Run ID, counts, and timings above are persisted run data. The current-run endpoints were read only; no ingestion artifact was modified by this assessment.
