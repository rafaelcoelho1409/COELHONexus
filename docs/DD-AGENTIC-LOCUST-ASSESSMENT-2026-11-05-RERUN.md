# DD Agentic AI — Locust rerun assessment (2026-11-05)

Run `fbcbf61f9dee4cd8a9929efa1ad7c226`, slug `locust`, namespace `coelhonexus-dev`.
Result: **completed all 24 pages** in 403.318 seconds (6m43s), from 17:11:16 to 17:17:59 UTC. This follows the earlier failed run documented in [the first DD assessment](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05.md).

## Run and LLM usage

| Measure | Result |
|---|---:|
| Pages normalized | 24 / 24 |
| Duration | 403.318 s |
| LLM calls | 85 |
| Auditor calls | 52 |
| Repair calls | 33 |
| Input tokens | 405,133 |
| Output tokens | 31,026 |
| Transient 504 responses | 2 |

The run completed despite two transient 504s. The run averaged about 12.6 counted calls per minute and 3.54 calls per page. The 52 auditor calls exceed one per page because calls can retry or make multiple model requests; they are not 52 concurrently active pages.

## Audit quality

The persisted assessments contain **44 findings across 15 pages**: 16 low, 28 medium, and no high severity findings.

| Category | Findings |
|---|---:|
| `malformed_heading` | 26 |
| `markdown_formatting` | 16 |
| `duplicate_boilerplate` | 1 |
| `other` | 1 |

Page index 23 (`API`) accounts for 24 findings. All say a heading should be followed by a blank line. That is not a Markdown defect: CommonMark permits an ATX heading to be followed immediately by content. These findings should be removed from the assessment baseline and must not drive repair. The DD auditor prompt and skill now explicitly prohibit reporting this case. [CommonMark ATX heading rules](https://spec.commonmark.org/current/#atx-headings)

The run recorded 17 accepted patches, 22 rejected patches, and 2 warnings. Page index 5 (`Configuration`) had three low-severity heading-level findings and all three proposals were rejected. The rejected changes avoided making unsupported edits, but the findings still indicate that the auditor needs better evidence thresholds for heading consistency.

## BD concurrency comparison

The most recent BD Agentic AI run inspected for this comparison was `8675dbc8812e4df3840ab7e54da1d280`: 275 pages, 513 counted LLM calls, 600.964 seconds, and no recorded errors.

| Pipeline phase | BD configured page concurrency | Maximum concurrent agent invocations implied by the code | DD before | DD now |
|---|---:|---:|---:|---:|
| Audit | 16 pages | 16 auditor invocations | 4 | 8 |
| Repair / normalization | 8 pages | Up to 16 specialist invocations in BD (each page can gather two specialists); 8 page jobs in DD | 4 | 8 |

BD sets `AGENT_MODEL_CALL_LIMIT=4`. This limits model calls within one agent invocation; it does **not** mean only four LLM calls run concurrently. BD's audit and repair are separate graph phases, so their configured maxima do not add together. Actual peak provider concurrency was not recorded in these run summaries; the figures above are code-configured upper bounds.

DD runs audit and normalization sequentially. Each phase now has a page semaphore of 8, up from 4. That raises available throughput while staying below BD's 16-page audit cap, which is appropriate for DD's large pages and the two 504s observed in this run. The rerun used the previous limit, so the speed change is not yet measured. The graph also resumes from persisted audit and normalization artifacts, so a retry need not repeat completed page work.

## Changes applied from this assessment

- Increased DD audit and normalization page concurrency from 4 to 8.
- Clarified in the DD auditor prompt and skill that ATX headings do not require a following blank line.
- Added immutable pre-Agentic page references to the published manifest and an Original / AI processed version switch in the Materials reader.
- Added standalone Agentic AI Start, Resume, Stop, and Wipe controls. These operate on the saved extraction; they do not repeat documentation extraction. Wipe restores the saved pre-Agentic pages and removes that candidate's AI artifacts and counters.
- Aligned the LLM usage and Graph/List controls with the Page progress column and made long page-progress lists scroll within their panel.

## Follow-up

The next Locust run should confirm that the eight-page cap reduces elapsed time without increasing 504s, then compare call and token counts per page. Quality follow-up should verify that the 24 blank-line complaints disappear and inspect the remaining 20 findings, especially the three rejected heading-level proposals on `Configuration`. No post-change run is included in this report.
