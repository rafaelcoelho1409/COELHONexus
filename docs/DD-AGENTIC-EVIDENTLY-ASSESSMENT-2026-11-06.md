# DD Agentic AI — Evidently run assessment, second run (2026-11-06)

Run `ada4a5124c1147958880dcebb0a71cdd`, slug `evidently` (Tier 1, `llms-full.txt`), namespace `coelhonexus-dev`.
Result: **completed all 70 pages** in 129.4 s (17:35:13 → 17:37:22 UTC), the first Evidently run on the build with the fixes from the [2026-11-05 assessment](DD-AGENTIC-EVIDENTLY-ASSESSMENT-2026-11-05.md). Sources: worker log, the run's MinIO artifacts (manifest, 70 assessments, 70 audits, 70 pages) and the LLM usage snapshot.

## Run and LLM usage

| Measure | This run | Previous run `81a2c3…` |
|---|---:|---:|
| Duration | 129.4 s | 249.7 s |
| LLM calls | 112 (110 audit, 2 repair) | 177 (127 audit, 50 repair) |
| Input / output tokens | 473,376 / 89,856 | 764,618 / 40,253 |
| Errors | 0 | 3 (`504`, recovered) |
| Audit / repair unavailable | 0 / 0 | 0 / 0 |
| Pages changed by the agentic stage | 0 | 2 (both wrong) |
| Distinct page titles | 70 of 70 | 61 of 70 |

Calls by model: codestral-2508 28, codestral-latest 22, laguna-xs-2.1 19 (17%), gemini-2.5-flash 15, 28 more across fourteen others. The output-token rise comes from gemini-2.5-flash (14 audit calls, 55,238 output tokens, about 3,900 each); it did not slow the run.

## What the fixes did

- **Export conventions:** the auditor reported 35 findings (55 last run); 26 of them quoted an MDX component, an `<img>` or a `theme=` fence opener and were discarded by the filter, 9 were kept. The prompt change cut the reports and the filter caught the rest.
- **Repair:** 1 page went to the repairer (2 calls), against 14 pages and 50 calls. It returned no patch, so nothing was applied.
- **Bad patches:** none. Page 19 still has `# ws.delete_project("PROJECT ID")` commented out and page 62 still has its closing sentence.
- **Titles:** 70 distinct. The repeats now read `Evaluations › Overview`, `Platform › Introduction`, and so on.
- **Published pages:** all 70 are identical to the extracted source. The bundle is valid Markdown for this stage, so the value of the agentic stage on this framework is the audit record (page kind, summary, findings), not edits.

## Remaining noise: "duplicate" findings that are not duplicates

The 9 kept findings on 7 pages:

| Pages | Finding | Verdict |
|---|---|---|
| 5, 16, 41, 52, 63, 65 | `duplicate_boilerplate` ×5, `duplicate_section` ×1: "repeated in the Markdown" | **All wrong.** The quoted text occurs once on the page (page 41's two code blocks share an opening line, not the block). Page 16's finding is the `Source:` line the prompt lists as never reportable. |
| 10 | `other` ×2: `GroupyBy` and `Metics` misspelled in prose | Correct; typos in the source. Review-only, never patched. |
| 41 | `markdown_formatting`, confidence 0.7: `The\`DataDriftPreset\`` has no space before the backtick | Correct and minor. Below the 0.90 repair threshold. |

The one repair call went to page 63 for a duplicate that is not one. The repair-side guard from last time (a removal must leave a surviving copy) would have rejected any patch, but the call was still spent, and the finding stays in the saved assessment.

## Change applied

- **A repeat finding must quote repeated text** (`page_audit/domain.py: sanitize_assessment`, `page_audit/params.py: DUPLICATE_CATEGORIES`): a `duplicate_boilerplate` or `duplicate_section` finding is discarded when its evidence (stripped) occurs fewer than twice on the page, with the warning "its evidence occurs once on the page, so nothing is repeated". `DUPLICATE_CATEGORIES` moved from `page_repair/params.py` to the audit node, which owns the finding categories; the repair guard reads it from there.
- **Effect on this run:** replayed on the saved pages, all 6 duplicate findings are dropped, repair-eligible findings go from 1 to 0 and the repair call on page 63 disappears. The 3 correct findings stay.
- **Trade-off:** a near-duplicate (two sections that differ by a word) is no longer reported. The repair step could not have acted on it either, because its evidence has to survive in another copy.
- **Checks:** `test_dd_page_agents_retry.py` passes with a new case (evidence once is dropped, evidence twice is kept) and the old convention case adjusted to a note that appears twice; `test_infra_boundary.py` passes.

## Not changed, follow-up

- **Needs a server redeploy** for the new filter. The published candidate of this run is already clean, so no Start or Wipe is needed.
- **Tier 1 is closed for MDX-shaped bundles.** Two Tier 1 frameworks (Locust 24 pages, Evidently 70) now run clean end to end. A bundle without `Source:` markers and 300+ pages (Pydantic) is the only untested Tier 1 shape.
- **The stored pages keep the export's conventions** (`theme={null}`, empty `<img>`, JSX components); cleaning them at ingestion would change every Mintlify page's hash and the A/B baseline, so it is still your call.
- **Tiers 2–4** have no agentic run yet.
