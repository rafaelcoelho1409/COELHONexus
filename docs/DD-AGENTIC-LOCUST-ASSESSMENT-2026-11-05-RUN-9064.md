# DD Agentic AI — Locust run 9064 assessment (2026-11-05)

Run `9064b4693771476297fd16f942a73572`, slug `locust`, namespace `coelhonexus-dev`.
Result: **completed all 24 pages** in 193.8 s (20:12:27 → 20:15:41 UTC). It is the first run with the audit and repair fixes from the [repair failure](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05-FAILED-RUN.md) and the [audit failure](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05-AUDIT-FAILURE.md) deployed. Sources: worker log, the run's MinIO artifacts, and the LLM usage snapshot.

## Run and LLM usage

| Measure | This run | Rerun `fbcbf6…` (done) | `0328ca…` (failed) | `24ec5a…` (failed) |
|---|---:|---:|---:|---:|
| Outcome | 24 / 24 | 24 / 24 | 23 / 24 | 0 / 24 |
| Duration | 193.8 s | 403.3 s | 375.0 s | 83.6 s |
| LLM calls | 51 (42 audit, 9 repair) | 85 (52, 33) | 72 (52, 20) | 59 (59, 0) |
| Input / output tokens | 236,680 / 9,928 | 405,133 / 31,026 | 358,559 / 15,829 | n/a |
| Errors | 1 (a 504, retried) | 2 (504) | 3 (504) | 0 |
| Pages with audit / repair unavailable | 0 / 0 | n/a | n/a | n/a |

About half the time and 40% fewer calls than the previous completed run. The summary lists no `audit_unavailable_pages` or `repair_unavailable_pages`; the 504 on an auditor call recovered through the retry loop. Auditor calls were spread over six models (nemotron 13, codestral-2508 12, codestral-latest 10, laguna 5, gemma 1, gpt-oss 1), so laguna's share fell from 64% to 12%. Repair ran for 5 pages and skipped 19, whose findings could not be patched.

## Audit quality

17 findings on 10 pages: 14 `markdown_formatting`, 1 `malformed_heading`, 2 `other`; 13 low and 4 medium. Eight were eligible for repair (patchable category, confidence ≥ 0.90): **2 applied, 6 rejected**.

By my reading of each finding against the page text, about 5 of the 17 are real defects (two empty links, a lost heading, two converter cross-references left as plain text) and about 12 are false positives:

| False positive | Pages | What the auditor called a defect |
|---|---|---|
| `#### NOTE` above a paragraph | 7 (×4) | "Note not formatted as a blockquote": a style preference, and the label of a converted admonition |
| Raw HTML embed | 1 | `<div><iframe>` "not standard Markdown": valid in CommonMark |
| Leading `---` | 11, 22 | "Unclosed front matter": it is the separator the bundle was split on |
| Code and console content | 4, 10 | A `\"` inside a Python fence; a long line in console output |
| List spacing | 6 | A rendering-neutral spacing difference |
| Blank line after a heading | 19 (×2) | Already prohibited in the previous prompt; the model repeated it at 0.8 |

The confidence gate from the earlier report did its job: all the 0.8-confidence ones never reached the repairer.

## Repair outcomes

| Page | Finding | Outcome |
|---|---|---|
| 2, 22 | Empty link `[Troubleshooting Installation]()` and `[Install Locust for development]()` | **Applied.** Both new anchors resolve to real headings on the page (`## Troubleshooting installation`, `## Install Locust for development`). |
| 7 (×4) | `#### NOTE` → blockquote | Rejected: the repairer put commentary in the `evidence` field. |
| 1 | iframe → image link | Rejected: surrounding context mismatch. |
| 4 | Escape inside a code fence | Rejected: target not in the evidence. |

The four page-7 rejections were correct but by accident. The proposed replacement was `> Because …`, which **drops the word `NOTE`**. Had the repairer echoed the evidence properly, all four would have been applied and lost their labels. That is a latent regression the rejection hid.

## Titles

24 page titles; one defect. Page 21's title was stored as `# aiolocust / asyncio`: the auditor quoted the heading line with its marker, the value is a substring of the page, so it passed validation and bypassed the heading fallback.

## Changes applied

- **Word-preservation guard** (`page_repair/domain.py: apply_patches`): a `markdown_formatting` or `malformed_heading` patch whose replacement drops any word of its target is rejected ("formatting patch removes words from the page"). Duplicate removals are unaffected. Both applied link patches pass it.
- **Title marker** (`output_title`): a leading `#`-marker is stripped from the proposed title before validation, so page 21 becomes `aiolocust / asyncio`.
- **Schema descriptions** (`agents/schemas.py`): `PagePatch.evidence` says "copied exactly, never an explanation", `rationale` says commentary goes there, and `PageAssessment.title` says "without a leading `#` marker". The tool schema carries these to the model.
- **Auditor prompt and skill**: raw HTML, label headings like `#### NOTE`, code and console contents, and the leading `---` separator are declared not reportable.
- **Repairer prompt**: formatting repairs keep every word of the target; explanations go in `rationale`.
- **Checks**: `scripts/server/test_dd_page_agents_retry.py` gained cases for the word guard (rejected, applied, and not applied to duplicate removals) and the title marker. All pass.

## Not changed, follow-up

- **Audit call count.** 42 auditor calls for 24 pages: most audits take two (one to read the skill file, one to answer). Putting the skill text in the prompt would roughly halve them, but how the agents use skills is your design choice, so I left it.
- **The leading `---`.** It comes from how the bundle is split into pages. Stripping it at ingestion would change page hashes; the prompt rule only stops it being reported.
- **Cross-references as plain text** (`see developing-locust.`, `See: running-in-debugger.`) are real converter artifacts but have no unambiguous target, so they correctly stay unpatched.
- **Next run:** check that page 7 raises no `#### NOTE` findings, that no applied patch removes words, and that no title starts with `#`.
