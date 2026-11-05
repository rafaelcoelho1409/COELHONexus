# DD Agentic AI — Locust failed run assessment (2026-11-05)

Run `0328cdaed59040879293b31b3d6c7b5b`, slug `locust`, namespace `coelhonexus-dev`.
Result: **failed** after 374.99 s (18:43:43 → 18:49:58 UTC) with 24 of 24 pages audited and 23 of 24 normalized:

```
ValueError: page repair specialist ended 3 times without a structured PagePatchBatch for page 14
```

It follows the [rerun that completed](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05-RERUN.md). Sources: worker log, the run's MinIO artifacts (`dd/locust/ingestion/agentic/<run>/`), and the LLM usage snapshot.

## What happened

| Measure | Result |
|---|---:|
| Pages audited / normalized | 24 / 23 |
| LLM calls | 72 (52 auditor, 20 repair) |
| Input / output tokens | 358,559 / 15,829 |
| Errors | 3, all `504` (2 auditor, 1 repair) |
| Distinct models answering repair calls | 6 |

Page 14 (`Event hooks`, 13,134 bytes) has an audit but no `assessments/0014.json` and no `pages/0014-…md`; every other page has all three. The auditors' two 504s were retried and recovered. The repair of page 14 ran about 3.2 minutes over its three attempts (one of them a 504) and then raised.

## Root causes

1. **Findings that could never become a patch were sent to the repairer.** The audit of page 14 has two findings, both `markdown_formatting`, both with the same evidence `(\*\*kwargs)`, both at confidence 0.8. The text is a backslash-escaped `**kwargs`, which CommonMark renders as the literal characters, so it is not a defect. The repairer is told to patch only at confidence ≥ 0.90 and `apply_patches` rejects anything below, so this call could only cost tokens or fail.
2. **A repairer that answers without the structured output fails the page.** The agent asks for a `PagePatchBatch` through `response_format`, and the Rotator pool serves a different model per call (six different ids across the 20 repair calls). The model name the agent sees is the pool alias, so LangChain most likely falls back to tool-based structured output; a model that ends its turn without calling that tool leaves `structured_response` empty. `resilient_ainvoke` re-asks, but each retry draws another model, so three misses in a row is plausible. The worker log does not keep the answers (the counter shows one of the three attempts was a 504), so what the other attempts returned is not recoverable; this cause is an inference from the code path and the error text.
3. **One cosmetic failure aborted the whole run.** `normalize_pages` gathers every page and cancels the rest on the first exception. The page was already valid (the audited original), yet 23 finished pages and about six minutes of work ended as a failed run. The BD pipeline already handles the same situation by degrading the page instead of failing the book.

## Evidence for the confidence gate

Outcome of the 24 pages of this run, by the auditor's confidence for each finding:

| Auditor confidence | Findings | Accepted patch | Rejected | Declined / not repaired |
|---|---:|---:|---:|---:|
| 0.8 | 6 | 0 | 1 | 5 (2 declined, 2 never repaired on page 14, 1 not patchable `other`) |
| ≥ 0.9 | 4 | 3 | 1 | 0 |

Small sample, but consistent with the repairer's own 0.90 rule: no finding below 0.9 produced an accepted patch.

## Changes applied

- **Repair eligibility** (`ai/dd/nodes/ingestion/page_repair/domain.py: repairable_assessment`): the repairer, and the patch validation, now see only findings in a patchable category with confidence ≥ `MIN_PATCH_CONFIDENCE`. A page with none skips the LLM call. The full audit is still saved unchanged; replay of an already-saved normalization keeps using it, so runs persisted before this change still resume.
- **Graceful repair failure** (`normalize_pages/service.py`): if the repairer cannot return a usable `PagePatchBatch` after its retries, the page keeps its audited original, a warning is logged, and the run continues. Cancellation is not swallowed.
- **Visible outcome**: each saved assessment now has a `repair` record (`status` = `not_needed` | `completed` | `unavailable`, finding counts, and the error when unavailable). Each output page carries `repair_status`, and the run summary lists `repair_unavailable_pages`.
- **Duplicate findings** (`page_audit/domain.py: sanitize_assessment`): findings with the same category and evidence merge into the most confident one, with a warning. Two findings on one span can only yield overlapping patches.
- **Auditor prompt and skill**: backslash-escaped punctuation (`\*\*kwargs`, `\_`, `\#`) is declared a valid CommonMark escape and not reportable, as was done for the blank line after an ATX heading in the previous assessment.
- **Checks**: `scripts/server/test_dd_page_agents_retry.py` gained offline cases for eligibility, merging, a page that never calls the repairer, and a page whose repairer never answers (it completes with `repair_status: unavailable`). All pass.

## Not changed, follow-up

- The **audit** stage has the same all-or-nothing behaviour: a page whose auditor never answers still fails the run. The 2 auditor 504s here recovered by retry, so it did not bite; BD degrades that case to deterministic findings and DD could follow.
- A `completed` or `unavailable` page is final for its run. Run Start again for another repair pass; Resume replays saved pages by design, because page outputs are immutable.
- Model quality per call is the Rotator pool's concern. Routing the repair task to models that reliably call tools would remove most of cause 2; it is outside this repository.
- Next run: confirm `repair_unavailable_pages` is empty or small, and that no 0.8-confidence finding reaches the repairer (repair calls should drop below the 20 seen here).
