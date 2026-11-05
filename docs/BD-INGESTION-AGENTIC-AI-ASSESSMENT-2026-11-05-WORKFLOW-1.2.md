# Book Distiller Agentic Ingestion Assessment — workflow 1.2 run, 2026-11-05

Third run, after the 1.2 quality fixes. Compared with [the previous run](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05.md) (workflow 1.1). The assessment data came from the `coelhonexus-dev` server API, the run's MinIO artifacts and the worker logs. The code changes made after this assessment are recorded below.

## Verdict

The run cost **2.8× the calls, 7.1× the input tokens and 3.2× the wall time** of the previous one, and the published text is **practically identical to the raw Docling extraction** (3 pages changed, all mechanical). It is a net regression in cost and coverage with two real gains (book metadata, mechanical fixes). The headline review count went up (130 → 171) mostly because a failed audit is counted as "needs review", not because more defects were found (unresolved issues fell 106 → 64).

## Run

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`), 275 pages |
| Agentic run | `50f20b88ba734a2d808730339a9955da`, workflow `1.2`, same Docling run `c94a1d68…` |
| Status | `done`, `complete_with_review`, no run error |
| Duration | 75.3 min (previous 23.2 min); audit ≈ 35 min, repair ≈ 40 min |
| Pages | 275 processed · 262 delivered · 13 blank discarded · 1 image-only · 171 flagged for review |

## Cost: what changed

| | Previous (1.1) | This run (1.2) |
| --- | ---: | ---: |
| LLM calls | 596 | 1,650 |
| Input tokens | 1,592,893 | 11,348,097 |
| Output tokens | 154,101 | 402,934 |
| Errors | 0 | 1 (a 504 timeout) |
| Page auditor | 364 calls · 0.83 M in · 2.3 K in/call | 994 calls · 2.73 M in · 2.7 K in/call |
| Structure repair | 153 calls · 0.51 M in · 3.3 K in/call | 337 calls · 5.60 M in · **16.6 K in/call** |
| Text repair | 78 calls · 0.25 M in · 3.2 K in/call | 315 calls · 3.01 M in · **9.6 K in/call** |
| Structured-output fallbacks (audit degraded) | 21 | **111** |

- The repair work is the same size (28 text and 30 structure work items, so at most ~116 specialist invocations), yet it made 652 model calls, about 5.6 per invocation, with a context that grows on every call. Almost all the extra tokens are here: 8.6 M of the 11.3 M.
- The 504 timeout was one structure-repair call.
- One small model took 687 of the 994 auditor calls. The pool mix changes between runs, so part of the regression is the model pool and not the code. The code cannot defend against that mix at present.

## Results: did quality improve?

| | Previous (1.1) | This run (1.2) |
| --- | ---: | ---: |
| Pages whose text changed vs raw Docling | 0 | 3 (pages 3, 37, 127) |
| Mechanical fixes | none | 5 control characters, 2 soft hyphens |
| Patch proposals produced | 20 (0 applied) | 4 (0 model patches applied) |
| Unresolved issues | 106 | 64 |
| Book title / author | raw filename, no author | "The Art of Cyberwarfare", Jon DiMaggio, confidence 0.98, no review needed |
| Pages flagged for review | 130 | 171 |

**Improved**
- Book metadata is now correct and accepted (the 0.85 threshold and filename-label fix worked).
- Control characters and soft hyphens are fixed in code (pages 3, 37, 127).
- Issue accounting is cleaner (merged duplicates, 106 → 64), and blank pages are still dispositioned deterministically.

**Not improved, or worse**
- **The model repair path produced no applied patch.** 652 repair calls gave 4 proposals; none was applied. Defects the run itself detected are all still in the text: `## ACKNOWL EDGMENT S` (p15), `## I NTRODUC T I ON` (p17), `L ANDSCAPE` (p21), `AT TACKS` (p23), `highprofile` (p76), `privatesector`, `humanreadable` (p183), `spe- cific` (p58), `engineer- ing` (p136), `viewdocum ent` (p250), `173.194.121 .36` (p222).
- **111 of 261 pages (43 %) were not LLM-audited**: both attempts failed and only the deterministic findings were kept. Those pages' real defects are unexamined, including page 11 (contents).
- **Review count is inflated.** Review reasons overlap: `degraded_audit` 111, `repair_errors` 51, `unresolved_issues` 59. A page whose audit merely failed is reported as "needs review" next to pages with real defects. The UI showed "51 pages need review" for yesterday's run while the report said 130, so the UI counter's source should be checked too.

## Root causes (from code and counters)

1. **No structured response is the dominant failure.** All 51 repair errors and the 111 audit fallbacks are `Input should be a valid dictionary or instance of …, input_value=None`. In langchain 1.4.6, when the model's last message has no tool call, the agent loop ends and sets `structured_response = None`. The model answered in prose or JSON text instead of calling the schema tool, and the text is thrown away. It is recoverable and is not being recovered.
2. **The agent loop is unbounded.** DeepAgents sets `recursion_limit = 9999`. When the model calls the schema tool with invalid arguments, the default `ToolStrategy(handle_errors=True)` feeds the error back and calls the model again, appending to the context each time. That explains 5.6 calls per invocation, 16.6 K in/call and the 504. Our own 2-attempt loop multiplies it.
3. **The model-facing schema asks for things the code already knows.** `PatchProposal` requires `page_number`, a 64-hex `source_sha256`, `issue_code`, `risk` and at least one full `evidence` object, with `extra="forbid"`. Free models mis-copy these, which fails validation or produces the no-tool-call case above. The skill and prompt tell the model to repeat all of them.
4. **Deterministic table cleanup does not exist** (see below), so contents and index tables go to a structure specialist that cannot repair them.

## Tables

31 Markdown tables on 29 pages.
- 13 are heavily space-padded (contents pages 9, 11–14, 58, 136, index pages 263–272): lines over 140 characters, mostly padding. This inflates every repair prompt.
- Contents tables keep the dot leaders (`Acknowledgments .  .  .  .  .  . xiii`), with fragments of the leader in the page-number cell.
- Index tables (263–273) have columns duplicated by merged cells (p267), and 4 pages (97, 142, 204, 267) have rows with repeated cell text.
- No table had ragged rows or a missing separator, so the structure is sound; the problems are padding, leaders and duplicates.

## Fixes recommended from this run

1. **Recover structured output.** If `structured_response` is `None`, parse the last AI message as JSON (strip code fences, take the first balanced object) against the schema before counting an attempt as failed.
2. **Bound the loop.** Add `ModelCallLimitMiddleware(run_limit=4, exit_behavior="end")` to the auditor and both specialists, so one invocation costs at most 4 calls and context cannot grow without limit. Treat a limit exit as a failed attempt.
3. **Slim the model-facing contract.** Ask the specialist only for `issue_id`, `target_text`, `replacement`, `rationale`, `confidence`, `risk` and optional anchors. Fill `page_number`, `source_sha256`, `issue_code` and `evidence` in code from the assigned issue. Update the skill and prompts to match. Apply the same idea to the auditor schema.
4. **Deterministic table normalizer** in `apply_mechanical_fixes`, recorded as a new mechanical fix `table_cleanup`:
   - compact cell padding;
   - strip dot leaders in contents tables, keeping title and page number;
   - drop empty rows and columns and columns duplicated from their left neighbour;
   - resolve `toc_noise` and `table_or_index_layout` issues the cleanup covers, and skip the specialist for them.

   Add table guidance to the `repair-markdown-structure` skill (GFM header and separator, consistent column count, no padding, merged cells, contents as title plus page number).
5. **Honest review accounting.** Report `unverified_pages` (audit failed) separately from `pages_requiring_review` (unresolved issue or repair error), expose both in the run status and show them separately in the UI.
6. **Raise concurrency only after 1–3 land** (audit 16, repair 8): today a higher limit would only multiply the retry spirals.

Expected effect of 1–3: calls back near 600–700, input tokens near 2 M, and real patches applied, since the 51 failures are the no-tool-call case.

## Implementation update

Applied after the assessment; this changes the workflow to **1.3** and the persisted manifest schema to **1.2**.

1. **Recover plain-text JSON and reduce schema burden.** Agent responses now recover the first balanced JSON object from the last assistant message when `structured_response` is absent. Audits return only page kind, confidence, issue fields and exact evidence strings; the application adds page numbers, evidence source and stable issue IDs. Repair agents return minimal drafts; the application fills page number, source hash, issue code and evidence from the assigned finding.
2. **Bound model retries.** The auditor and both repair specialists stop after four model calls per invocation. A call-limit exit is reported as a failed attempt instead of retrying the same capped invocation. Existing outer retries remain bounded separately.
3. **Normalize tables in code.** Valid Markdown tables are compacted, empty rows and columns are removed, exact duplicate adjacent columns are collapsed, and clear contents dot leaders are stripped while retaining titles and page references. The cleanup is recorded as `table_cleanup`; a table issue is considered resolved only when its cited source excerpt was changed. The structure-repair skill now requires valid, consistent GitHub-flavored table output and preservation of distinct cell values.
4. **Separate review from verification.** `pages_requiring_review` now means unresolved findings or repair errors. Pages whose LLM audit failed or exceeded its size limit are reported separately as `unverified_pages`, with counts in the quality report and run API. They no longer inflate the known-review count. Unverified pages remain visible in the page list.
5. **Update the reader controls.** Graph/List and Rendered/Markdown source are segmented toggles. Pipeline controls sit above the Chapter progress panel at the same width. The Agentic AI progress box groups processed, audited, review, unverified, chapter and active-work counts; audit warnings are labeled separately.
6. **Increase bounded concurrency.** With the retry cap in place, page audit concurrency is 16 and repair concurrency is 8 (up from 4 each) to reduce the time spent waiting on independent pages.

At the time of this implementation update, the expected call and token savings were estimates and the changed workflow had not yet been run against this book. The subsequent workflow 1.3 live run is assessed in [the follow-up report](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.3.md). No tests or browser checks were run during that earlier implementation pass. Static validation passed: Python syntax parsing for the changed backend modules, frontend typecheck, generated API client and contract checks, targeted Biome and ESLint checks, and `git diff --check`.

## Still open outside this assessment

- The older `e2e/bd.spec.ts` (Pipeline/Materials split) is still stale.
