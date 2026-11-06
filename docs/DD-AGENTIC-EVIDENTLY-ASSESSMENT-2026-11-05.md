# DD Agentic AI — Evidently run assessment (2026-11-05)

Run `81a2c3ec30ac419d96749e9d519ed61b`, slug `evidently` (Tier 1, `llms-full.txt`), namespace `coelhonexus-dev`.
Result: **completed all 70 pages** in 249.7 s (23:07:29 → 23:11:39 UTC), ingestion and Agentic AI together. The bundle was 467 KB and split on its `Source:` markers into 70 pages. This is the second Tier 1 framework through the agentic stage, after Locust (24 pages), and the first Mintlify/MDX one. Sources: worker log, the run's MinIO artifacts and the LLM usage snapshot.

## Run and LLM usage

| Measure | Evidently | Locust run `934d8d…` |
|---|---:|---:|
| Pages | 70 | 24 |
| Duration | 249.7 s | ≈395 s |
| Seconds per page | 3.6 | 16 |
| LLM calls | 177 (127 audit, 50 repair) | 79 |
| Calls per page | 2.5 | 3.3 |
| Input / output tokens | 764,618 / 40,253 | 511,262 / 18,170 |
| Errors | 3, all `504` on the auditor, recovered by retry | 4 |
| Audit / repair unavailable | 0 / 0 | 0 / 1 |

Audit calls by model: codestral-2508 40, laguna-xs-2.1 36 (28%), codestral-latest 32, ising-calibration-1.5-31b 8, gemma-4-31b 3, others 8. With laguna down to 28% of the pool draw the run was fast: three times as many pages in less time. Nothing here is a scale problem.

## Audit quality

55 findings on 34 of the 70 pages: 47 `markdown_formatting`, 4 `duplicate_boilerplate`, 3 `other`, 1 `encoding_artifact`; 44 low and 11 medium. **42 of the 55** (on 25 pages) report the export's own conventions, not defects:

| What the auditor reported | Findings | Why it is not a defect |
|---|---:|---|
| Fence opener ```` ```python theme={null} ```` ("extra parameter") | 16 (page 3 alone has 11) | 288 of the bundle's fence openers carry it; the downstream corpus normalizer strips it |
| `<img alt="" />` and `<iframe>` ("empty image", "iframe not described") | 17 | 119 placeholders in the bundle, all without a source; also handled downstream |
| `<Note>`, `<Info>`, `<Warning>`, `<Check>`, `<Tip>`, `<CardGroup>` ("raw HTML") | 9 | MDX components, converted downstream |

The counts are by the rule now in code (below). Of the other 13, two are claims the source contradicts, five are valid Markdown (padded tables, bold inside a heading), one is list spacing, and the rest are repeats (`<Check>` blocks) or a URL escape at 0.7.

## Repair: 2 patches applied, both wrong

25 findings were eligible (patchable category, confidence ≥ 0.90); 14 pages went to the repairer, using 50 calls (3.6 per repaired page) to produce 2 applied patches and 23 rejections. Twenty rejections were "target does not occur exactly once" (the same `<img>` or fence opener repeated), two were the word guard from the [run 9064](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05-RUN-9064.md) fix doing its job live, and one was an evidence mismatch.

The two that were applied should not have been:

- **Page 19, `Manage Projects`: a destructive call was made live.** The page documents deleting a project, and shows the call commented out on purpose inside a code block in a `<Tab>`:

  ```
  # ws.delete_project("PROJECT ID")
  ```

  The patch turned it into `ws.delete_project("PROJECT ID")`. The fence guard did not stop it because the block sits four spaces deep inside the component, and the guard only recognized fences indented at most three spaces (the CommonMark rule for plain Markdown). The published page now shows an active delete call.
- **Page 62, `Frequently Asked Questions`: the only copy of a sentence was deleted.** The patch removed `Use the menu on the left to explore other questions.` as a "duplicate", but the sentence occurs once on the page.

## Titles: a regression of the agentic stage

70 pages, 61 distinct titles: `Overview` ×8, `Introduction` ×2, `Synthetic data` ×2. The pre-agentic split already tells these apart ("Platform › Overview") using each page's own `Source:` address (`source_path`). The agentic stage left it out of its page references and disambiguated by `url`, which for every page cut from one bundle is the bundle's address (`…/llms-full.txt`). With the same address on every page there is nothing to tell them apart by, so the repeats stayed. Locust had no repeated titles, so it never showed.

## Changes applied

- **Fences at any indentation** (`page_repair/domain.py: overlaps_fenced_code`): a fence counts however deep it is nested, so code inside `<Tab>`, `<Step>` and similar is never edited.
- **A duplicate must have a surviving copy** (`duplicate_survives`): a `duplicate_boilerplate` or `duplicate_section` patch is rejected when the text it removes would no longer appear on the page ("removing it would leave no copy of the text").
- **Export conventions are not findings** (`page_audit/patterns.py`, `domain.py: sanitize_assessment`): a `markdown_formatting`, `malformed_heading` or `other` finding whose evidence opens with a JSX component, an `<img>`, `<iframe>` or `<video>` tag, or a fence opener with a `theme=` attribute is discarded, with a warning. Repeat findings (`duplicate_*`) are unaffected. On this run's findings that is 42 of 55; the findings eligible for repair fall from 25 to 3, and those three are now rejected or never patched (page 19 and page 62 by the guards above, page 64 is list spacing), so the repair step would have made 0 calls on these pages instead of 50.
- **Titles** (`ingestion/ai/service.py`, `ai/dd/graphs/ingestion/state.py`, `normalize_pages/service.py`): page references now carry `source_path`, and the title disambiguation uses it before falling back to `url`.
- **Auditor prompt and skill**: MDX components, `<img alt="" />`, fence attributes, padded tables, bold inside headings, the `Source:` line and a commented-out line in a code block are listed as never reportable.
- **Checks**: `test_dd_page_agents_retry.py` passes. New cases cover the nested fence, a repeat removal with one copy and with two, the convention filter, and distinct titles for pages split from one bundle.

## Not changed, follow-up

- **This run's published pages 19 and 62 hold the two bad patches.** After the redeploy, run Start again for a new candidate, or Wipe to restore the saved originals.
- **The pages keep the export's conventions.** `theme={null}`, the empty `<img>` tags and the JSX components stay in the stored pages (the downstream normalizer handles them). Cleaning them at ingestion would make the pages cleaner for the Materials reader but changes every Mintlify page's hash and the A/B baseline, so it is your call.
- **Resuming an older run.** A page saved by an earlier run keeps its patches in its assessment, and resuming replays them through today's rules. If a past patch would now be rejected (like the two above), the replay no longer reproduces the saved page and the resume fails with a clear error; Start a new candidate instead.
- **Tier 1 gate.** Mechanics hold at 70 pages in four minutes; the problems found were specific to the MDX shape and are now guarded. A third Tier 1 framework of a different shape (Pydantic, 300+ pages without `Source:` markers) would close the tier.
