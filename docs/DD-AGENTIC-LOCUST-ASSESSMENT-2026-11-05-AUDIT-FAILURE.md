# DD Agentic AI — Locust audit failure assessment (2026-11-05)

Run `24ec5ae45dc34c2f91f0e49c731599f8`, slug `locust`, namespace `coelhonexus-dev`.
Result: **failed** after 83.6 s (19:29:10 → 19:30:33 UTC) in the audit phase, with 11 of 24 pages audited and none normalized:

```
ValueError: page auditor ended 3 times without a structured PageAssessment for page 10
```

It follows the [repair failure](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05-FAILED-RUN.md) of the same day. This one is in the audit stage, which that report listed as the remaining all-or-nothing step.

## What happened

| Measure | Result |
|---|---:|
| Pages audited | 11 of 24 (1, 5, 6, 7, 12, 14, 16, 17, 19, 22, 23) |
| LLM calls | 59, all auditor |
| Errors | 0 (no 504s) |
| Calls by model | laguna-xs-2.1 38 (64%), gemma-4-31b 17, minimax-m3.1-flash 2, gpt-oss-120b 1, step-5-preview 1 |

There were no provider errors: each of the three attempts on page 10 returned normally, without a structured response.

## Root cause

Page 10 (`Running without the web UI`, 4.6 KB) is ordinary. I ran the same auditor on it 18 more times in the worker pod: 16 returned a `PageAssessment`, 2 did not. Both misses ended on `laguna-xs-2.1`, whose last turn had `finish_reason: stop` and no tool call. Its text was the complete, schema-valid assessment in a fenced block:

````
Based on my analysis of the Markdown content, here is the PageAssessment:

```json
{"page_kind": "documentation", "confidence": 0.95, "title": "Running without the web UI", ...}
```
````

The agent asks for the answer through a tool call, so `structured_response` stayed empty and the answer was thrown away. The pool served laguna for 64% of this run's calls, which is why three misses in a row on one page happened. The repairer has the same exposure. BD already recovers this case by reading JSON from the last assistant message; DD did not.

A second weakness made it fatal: any audit failure aborted the run, although the extracted page is valid without an audit.

## Changes applied

- **Recover the answer** (`ai/dd/agents/domain.py: structured_or_recovered`): when `structured_response` is empty, the first JSON object in the last assistant message that fits the schema is used. Only the last assistant message is read, and JSON that does not fit the schema still counts as no answer and is asked again. Applied to the auditor (`PageAssessment`) and the repairer (`PagePatchBatch`); the retry loop's accept check uses the same rule.
- **Graceful audit failure** (`audit_batch/service.py`): if the auditor still cannot answer after its retries, the page goes on as extracted with an empty assessment, a warning, and an `audit_status: unavailable` record that carries the reason. Cancellation is not swallowed.
- **Visible outcome**: the run summary now lists `audit_unavailable_pages` next to `repair_unavailable_pages`.
- **Checks**: `scripts/server/test_dd_page_agents_retry.py` gained offline cases for a fenced assessment, only the last assistant message being read, JSON of the wrong shape, a fenced `PagePatchBatch`, and an auditor that never answers (the audit completes as `unavailable`). All pass.

## Not changed, follow-up

- A degraded page is final for its run (audit artifacts are immutable); Start a new candidate to audit it again.
- Which models the pool serves is outside this repository. One that ignores the structured-output tool is now harmless to correctness, but each recovered answer is still a slower, text-based path.
- Next run: confirm `audit_unavailable_pages` and `repair_unavailable_pages` are empty, and compare the call count per page with the 52 audit calls for 24 pages in the completed rerun.
