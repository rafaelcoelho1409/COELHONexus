# DD ingestion refinement loop — Tiers 1–4

This guide defines the repeatable refinement loop for Docs Distiller ingestion. Tier 5 (GitHub) is explicitly out of scope.

## What a benchmark run does

The benchmark endpoint forces one catalog source tier and runs the same full path as normal ingestion:

1. extract from the requested source;
2. prepare and split pages;
3. run Agentic AI audit, repair and normalization;
4. build vaults and publish a manifest.

The forced tier has no fallback. If that source is unavailable or unusable, the benchmark fails and records why. Normal catalog ingestion keeps its existing priority and fallback behavior.

Benchmark pages and manifests are written beneath a unique hidden storage slug, so a benchmark cannot replace the framework's normal catalog corpus. The real framework slug is still used for the single-flight lock; run benchmarks sequentially. Hidden benchmark artifacts remain available for inspection and are excluded from the visible DD library.

Each report records the framework, requested tier, source attempts, build metadata (including Git SHA when configured), normalizer version, extraction and post-processing sizes, Agentic AI quality summary, phase timings, vault status, publish count and terminal outcome. Reports are durable in MinIO under `dd-benchmarks/{round_id}/{run_id}.json`; the round ID groups runs made against one code revision or experiment.

## Run the loop

1. Choose one representative source for each tier and record the deployed build metadata.
2. Run one benchmark at a time. Keep one `round_id` for all runs against the same deployed build; use a new round after redeployment.
3. Follow each run through the regular ingestion progress route. Its response includes a `benchmark` object.
4. Read the durable report and inspect a small sample of original and processed pages for content fidelity, links, code examples, ordering and missing sections. Quality counters alone do not establish correctness.
5. After each tier, apply code fixes locally and record them as pending deployment and validation. With `skaffold run`, these edits do not change the active application, so remaining runs in this round still measure the recorded deployed build.
6. After Tier 4, redeploy the accumulated fixes and repeat the representatives in a new round to validate them. Continue until results converge across all four tiers; rerun any tier affected by later shared changes.
7. Consider a tier ready when extraction and publishing succeed, expected content is present, Agentic AI and vault backfill complete, and sampled output preserves the source's teaching content. Record accepted review items and known source-specific limitations.

Do not start the next benchmark until the current run is terminal. Ingestion uses a shared LLM pool and a global one-at-a-time lock; parallel full-pipeline runs make results less comparable and can exhaust provider capacity.

## API

Start a full-pipeline benchmark:

```http
POST /api/v1/dd/debug/benchmarks/{slug}
Content-Type: application/json

{"tier": 3, "round_id": "ingestion-r1"}
```

`tier` must be `1`, `2`, `3` or `4`. The `round_id` must start with a letter or digit and contain at most 48 letters, digits, dots, underscores or hyphens.

Read live progress and benchmark metadata:

```http
GET /api/v1/dd/ingestion/runs/{run_id}
```

List reports in a round, or read one report:

```http
GET /api/v1/dd/debug/benchmarks/{round_id}
GET /api/v1/dd/debug/benchmarks/{round_id}/{run_id}
```

The start response and report include `storage_slug`. Use it to inspect the isolated corpus and page-level quality records:

```http
GET /api/v1/dd/ingestion/{storage_slug}/manifest
GET /api/v1/dd/ingestion/{storage_slug}/pages/{idx}?version=processed
GET /api/v1/dd/ingestion/{storage_slug}/pages/{idx}?version=original
GET /api/v1/dd/ingestion/{storage_slug}/pages/{idx}/quality
```

If the framework is already ingesting, the start endpoint returns the existing `locked` response. A tier URL absent from that framework's catalog entry returns `400`; choose a framework that declares the requested source.

## First-pass representatives

Use the smallest useful representative first, then run the larger regression pick after that tier's issues are fixed. Page counts below come from the DD ingestion assessment dated 2026-11-07; re-check the current catalog and source before starting because upstream docs change.

| Tier | First pass | Follow-up | Coverage focus |
|---|---|---|---|
| 1 — `llms_full` | Unsloth (~202 pages) | Pydantic (~328 pages) | Multi-part bundle handling, banner removal, monolith splitting and navigation |
| 2 — `llms_txt` | Optimum (11 pages in this run) | React (~180 pages) | Index parsing, page ordering, section metadata and JSX residue |
| 3 — `sitemap` | DeepAgents (70 URLs → 110 pages in this run) | FastAPI (~153 pages) | Sitemap filtering, URL extraction and a second documentation-site generator |
| 4 — `docs` | Asyncio (20 URLs → 118 pages in this run) | Celery for Python (~234 pages) | Sphinx inventory/toctree traversal, fragment handling and autodoc-heavy pages |

These are candidate sources, not a claim that they are the only or always-best corpus. Confirm that the selected catalog entry has the requested tier URL before each run. Tier 1's banner issue and Tier 4's Sphinx edge cases are included intentionally so the refinement loop exercises known risk areas.

## Run status

### Tier 1 — Unsloth (`llms_full`)

| Field | Result |
|---|---|
| Round / run | `tier1-r1` / `f9a9483f273a490e8c5fdf74fa33d202` |
| Source | `https://unsloth.ai/docs/llms-full.txt` |
| Build | service `1.0.0`; Git SHA unavailable; corpus normalizer v13; agentic normalizer `dd-ingestion-agentic-v2` |
| Outcome | Completed; extraction, agentic normalization, vault backfill and publish succeeded |
| Extraction | 1 bundle; 3,225,933 bytes; 162,145 ms |
| Preparation | 1 bundle split into 205 pages; 2,702 ms; 0 stubs or duplicates dropped |
| Agentic stage | 2,700,334 ms (~45 min); 205 pages |
| Vault / publish | Vault backfill done in 19,337 ms; 205 pages published |
| Total | 2,884.65 seconds (~48 min) |

**Quality observed:** 127 pages had 339 findings: 195 `markdown_formatting`, 67 `duplicate_section`, 39 `duplicate_boilerplate`, 25 `malformed_heading`, 8 `encoding_artifact`, and 5 `other`. There were 152 low- and 187 medium-severity findings. Four patches were accepted and 13 rejected; 47 pages had repair unavailable, and 56 pages were marked for review. Mechanical cleanup collapsed 322 blank-line runs, stripped 19 permalink fragments and 10 zero-width characters, among other smaller fixes.

The run made 937 LLM calls (8,591,191 input and 379,359 output tokens) and recorded 53 provider errors: 43 HTTP 504s, 9 connection errors and 1 API error. `page_auditor` made 210 calls with 22 errors; `page_repair` made 727 calls with 31 errors. Original page content was preserved when repair failed.

**Sample findings:** one split page became empty after deterministic cleanup but was still published. Several valid GitBook template blocks, headings inside a stepper, balanced raw HTML and an HTML table were also reported as defects. These caused avoidable findings and repair traffic. Duplicate findings also need conservative handling because removing repeated documentation can lose context.

**Local fixes applied; not yet validated on a redeployed build:**

- Drop pages made empty by deterministic normalization before manifest creation, reindex retained pages, and report `empty_pages_dropped`.
- Recognize valid GitBook component syntax and content inside balanced components; suppress findings against balanced raw HTML and tables.
- Clarify the auditor prompt about GitBook blocks, raw HTML and custom attributes.
- Restrict automatic repair to high-confidence, syntax-safe categories; require audit confidence of at least 0.95 and stop attempting duplicate-removal repairs.
- Bound repair/check calls and use a shorter repair retry delay; only mark eligible unresolved repair issues for review.
- Insert a separator after a leading GitBook emoji shortcode in a heading; corpus normalizer version is now 14 and the agentic normalizer version is v3.

**Round `tier1-r1` disposition:** the baseline pipeline completed, but Tier 1 had not converged. The fixes above needed redeployment and a regression run. Tier 2–4 results are tracked below as they are run. Tier 5 remains out of scope.

**Redeployed validation — round `dd-refine-2026-11-11-r3`:** run `daf55531bed44073a11d59865149507a` completed end to end with all 204 retained pages published and vault backfill done. Extraction took 60.94s, preparation 24.19s, Agentic processing 1,956.05s (~32m36s), backfill 50.72s, and total runtime was 2,092.19s (~34m52s). The run used normalizer v14 and `dd-ingestion-agentic-v3`. Preparation dropped the one empty split page identified in the prior pass; no empty pages were published.

Quality recorded 99 findings on 35 pages (72 `duplicate_boilerplate`, 18 `duplicate_section`, 3 `encoding_artifact`, 1 `malformed_heading`, 4 `markdown_formatting`, 1 `other`), with 40 low- and 59 medium-severity findings. There were 22 audit-unavailable pages and 23 review-required pages; repairs had 0 accepted and 2 rejected patches. The run made 208 calls and recorded 62 provider errors (57 HTTP 504s, 1 connection error and 4 other API errors). It applied 322 blank-line collapses, 164 emoji-heading spacing fixes, 19 permalink removals, 3 trailing-rule removals and 10 zero-width-character removals.

**Assessment and local fixes pending redeployment:** the issue scan confirmed real content problems on a lingering internal note and one inconsistent numbered heading. It also found false positives for repeated text under distinct model/configuration examples, GitBook block-marker repetition, valid HTML numeric entities, escaped model identifiers, and valid colon-aligned GFM tables. I added deterministic guards for standalone escaped identifiers, valid numeric entities, valid table-row context and duplicate findings made only of structural export tags; the auditor prompt now clarifies contextual repeats, export markers, HTML entities and alignment syntax. These refinements still need a deployed regression. The durable report is `dd-benchmarks/dd-refine-2026-11-11-r3/daf55531bed44073a11d59865149507a.json`.

**Redeployed validation — round `dd-refine-2026-11-12-r1`:** run `de8d88803832495f94ad9cc0f1b0bfd4` completed with all 205 pages published and vault backfill done. The source bundle was 3,235,279 bytes; extraction took 54.29s, preparation 17.01s, Agentic processing 748.95s (~12m29s), backfill 21.14s, and total runtime was about 13m36s. The run used normalizer v14 and `dd-ingestion-agentic-v3`. Preparation recorded one empty-page drop; the final report says 205 pages published.

The report recorded 181 findings on 71 pages: 38 `duplicate_boilerplate`, 56 `duplicate_section`, 9 `malformed_heading`, 77 `markdown_formatting`, and 1 `other`. There were 70 low-, 110 medium- and 1 high-severity finding. No audits were unavailable; repair was unavailable on pages 100, 117 and 162. Four pages require review; zero patches were accepted and one rejected. The run made 248 LLM calls with 11 provider errors (10 HTTP 504s and 1 connection error). Its durable report is `dd-benchmarks/dd-refine-2026-11-12-r1/de8d88803832495f94ad9cc0f1b0bfd4.json`.

**Assessment and local fixes pending redeployment:** sampled original and processed pages are identical, so the Agentic stage did not damage those examples. Most sampled findings were false positives: valid ATX headings (including bold headings) were called malformed, source tab/platform alternatives were called duplicates, and one `other` finding asked for a more formal writing style. Real source defects remain: two pages contain the invalid target `[llama.cpp](llama.cpphttps://github.com/ggml-org/llama.cpp)`, and a Muse Glimmer page has a malformed Dynamic 4-bit phrase/link. The high-severity duplicate is a transition sentence repeated before separate tool-calling example groups; high severity overstates its impact. I widened the deterministic valid-heading filter, clarified tab-scoped repetition and editorial-style guidance, and added a narrow idempotent normalizer fix for the exact duplicated `llama.cpp` URL prefix (normalizer v15). Other malformed source text remains for review because its intended repair is not unambiguous. These local changes did not affect the r1 run and need a deployed regression.

### Tier 2 — Optimum (`llms_txt`)

| Field | Result |
|---|---|
| Round / run | `dd-refine-2026-11-11-r2` / `2efed9f4e115479eaabd716025843bc3` |
| Source | `https://huggingface.co/docs/optimum/llms.txt` |
| Build | service `1.0.0`; Git SHA unavailable; corpus normalizer v14 |
| Outcome | Failed at publish; no pages published |
| Extraction | 11 pages; 86,659 bytes; 2,287 ms |
| Preparation | 11 pages retained; no split, empty pages or duplicates; 472 ms |
| Agentic stage | Failed after 76.80 seconds; audit and normalize nodes completed for all 11 pages |
| LLM calls | 11 calls; 9 `OpenAIConnectionError` errors in `page_auditor` |
| Error | `ValueError: page assessments must cover every source page in order` |

**Assessment:** extraction and preparation succeeded. The audit node returned the same number of assessments as source pages, but publish found their index sequence did not exactly match the source-page sequence. The current error does not report whether indices were merely reordered or whether there was a duplicate/missing pair, so the precise shape is unknown. Provider connectivity also degraded the audit quality; fallback assessments kept processing moving, but publication then failed on the coverage check.

**Local fix applied; pending deployment and validation:** publish now checks unique, exact index coverage for both assessments and normalized outputs, then orders them to match the source manifest. This preserves strict missing/duplicate detection without requiring downstream lists to arrive in the same order. The isolated benchmark staging objects were cleaned up; the durable report remains available under `dd-benchmarks/dd-refine-2026-11-11-r2/`.

**Redeployed validation — round `dd-refine-2026-11-11-r3`:** run `251663da35e24667bec7250a8c393383` completed successfully against normalizer v14 / agentic normalizer v3. All 11 pages published, vault backfill completed in 2.52 seconds, and total run time was 135.05 seconds. This confirms the prior publish-order failure is fixed on the deployed build. The finalized manifest keeps the intended navigation order, which differs from numeric page-index order.

There were 13 audit calls and 10 provider errors (9 connection errors, 1 HTTP 504). One page (index 10) remained unaudited and was preserved unchanged. The 14 quality findings were low-severity duplicate-boilerplate findings across two pages; their exact repeated parameter descriptions were not auto-removed, appropriately avoiding loss of potentially meaningful API context. No code change is indicated by these findings. The durable report is `dd-benchmarks/dd-refine-2026-11-11-r3/251663da35e24667bec7250a8c393383.json`.

**Redeployed validation — round `dd-refine-2026-11-12-r1`:** run `c1c7d7ea7bba4cc3b011d4923751f560` completed with all 11 pages published and vault backfill done. Extraction took 2.90s, preparation 0.54s, Agentic processing 189.32s, backfill 0.75s, and total runtime was about 193.5s. The run used normalizer v14 and `dd-ingestion-agentic-v3`.

Quality recorded 8 `markdown_formatting` findings on 4 pages: 3 high, 1 medium and 4 low. One audit (page 0) was unavailable after two provider errors (one HTTP 504 and one connection error); one page required review. The run made 13 LLM calls, published all pages, and had no accepted or rejected patches. Its durable report is `dd-benchmarks/dd-refine-2026-11-12-r1/c1c7d7ea7bba4cc3b011d4923751f560.json`.

**Assessment and local fixes pending redeployment:** page 2 contains a genuine readability issue: three or more hardware/platform labels and descriptions are indented as a code block rather than a list. Pages 4 and 10 contain source-export corruption where method names, code links and JSON metadata are concatenated; the canonical summaries, parameters and return sections follow separately. A local normalizer pass now restores a method heading and source link while removing only that duplicated metadata line, and converts the clearly repeated indented label/description pattern into bullets. Page 7's four low findings claim missing spacing after headings even though the cited source shows a blank line; a deterministic evidence check discards that contradiction. These local fixes raise the normalizer to v17 and need a deployed rerun. The Tier 2 pipeline itself succeeded, but the broken pages and one unavailable audit mean the tier is not converged.

### Tier 3 — DeepAgents (`sitemap`)

| Field | Result |
|---|---|
| Round / run | `dd-refine-2026-11-11-r2` / `fced1526a171451fbf74b40d3be3a036` |
| Source | `https://docs.langchain.com/sitemap.xml` |
| Build | service `1.0.0`; Git SHA unavailable; corpus normalizer v14; agentic normalizer `dd-ingestion-agentic-v3` |
| Outcome | Completed; 110 pages published |
| Extraction | 1,727 sitemap entries filtered to 70 pages; 2,014,758 bytes; 28,541 ms |
| Preparation | 70 pages split to 110 agentic inputs; 12,448 ms; no empty pages, stubs or duplicates dropped |
| Agentic stage | 1,059,553 ms (~17.7 min); all 110 audits, normalizations and publish steps completed |
| Vault / publish | Vault backfill done in 10,574 ms; 110 pages published |
| Total | 1,111.26 seconds (~18.5 min) |

**Quality observed:** 6 findings across 6 pages: 4 `markdown_formatting`, 1 `malformed_heading` and 1 `generated_title`; 2 low- and 4 medium-severity. No repair was unavailable. One page (index 11) could not be audited after structured and JSON recovery calls both received HTTP 504 responses; it was kept unchanged and is the only review-required page. The run also discarded unsupported findings about valid export syntax, unproven duplicates and non-exact evidence.

**Sample assessment:** the 110 outputs retain the main DeepAgents pages, including the overview, quickstart, customization, subagents, backends and skills. The sitemap filter also includes supporting concepts pages. Oversized source pages were split successfully. A sampled customization child has a `## System prompt` heading but was displayed with its first prose sentence as the page title; this is a deterministic title-selection bug. Several audit findings appear to be false positives or style preferences: a Mermaid code fence, escaped `\|` text, a bold field name with inline-code type, provider-specific code examples, headings introducing a shared table, and a concise `Productivity` section heading.

**Local fixes applied; pending deployment and validation:**

- Tier 2 publish fix: enforce unique, exact page-index coverage and restore source order instead of rejecting complete lists solely for arriving in a different order.
- Drop auditor findings whose evidence exists only inside fenced code blocks, protecting Mermaid diagrams and code examples even if the LLM ignores the prompt.
- Refine auditor guidance for escaped pipes, provider-specific examples, valid field-reference styling, shared content under heading sequences, and concise H2 titles on split pages.
- When a split page has no H1, use its first ATX heading outside code fences as its display title before falling back to prose. This fixes the observed `System prompt` title mismatch and supports section titles such as `Productivity`.

**Round r2 disposition:** extraction, normalization, publish and vault backfill succeeded, but that pass was not converged because one page was unaudited and the page-title and auditor false-positive fixes still needed a deployed regression run. The durable report remains under `dd-benchmarks/dd-refine-2026-11-11-r2/`.

**Redeployed validation — round `dd-refine-2026-11-11-r3`:** run `d82f58b91dcf45008f0bd775a8ab0cbb` completed successfully: 70 sitemap pages became 110 Agentic pages, all 110 were normalized, all pages were published, and vault backfill completed. Extraction took 63.46s, preparation 33.16s, Agentic processing 982.07s, backfill 16.61s, and total runtime was 1,095.67s (~18m16s). The run used normalizer v14 and `dd-ingestion-agentic-v3`.

The display-title fix is validated: the `customization` child now displays as **System prompt**, and the split integration page displays as **Productivity**. All seven sampled pages from the previous false-positive cases had no retained issues; the auditor correctly discarded fenced-code, export-syntax, and non-exact-evidence findings. The full run retained 3 findings on 2 pages: two issues on Checkpointer integrations (a plain-text section label and a missing diagram referenced by the source text) and one plain-text migration heading. These are present in the source export and are reasonable review findings; no automatic content rewrite is warranted.

Provider reliability remains the main limitation: 12 of 110 pages (indices 20, 27, 35, 41, 46, 47, 48, 49, 50, 77, 104 and 106) were kept unchanged with audit unavailable after provider failures. Final counters show 103 calls, 29 errors (28 HTTP 504s and 1 connection error). The bounded retry behavior allowed the run to finish and publish, but this audit-unavailable rate is too high for convergence. The durable report is `dd-benchmarks/dd-refine-2026-11-11-r3/d82f58b91dcf45008f0bd775a8ab0cbb.json`.

**Redeployed validation — round `dd-refine-2026-11-12-r1`:** run `b6ae423d376945bf83f30ea25684c38e` completed end to end: 70 sitemap URLs produced 110 pages, all 110 were normalized and published, and vault backfill completed. Extraction took 28.24s, preparation 12.76s, Agentic processing 316.55s, backfill 11.39s, and the report duration was 369.08s. The run used normalizer v14 and `dd-ingestion-agentic-v3`.

The run recorded 22 `markdown_formatting` findings on 11 pages (19 low, 3 medium), with no accepted or rejected patches. One audit (page 48) was unavailable after an HTTP 504 and that source page was preserved unchanged; no repairs were unavailable. The auditor made 116 calls and recorded 5 provider errors (four HTTP 504s and one connection error). The completed report is available from `/api/v1/dd/debug/benchmarks/dd-refine-2026-11-12-r1/b6ae423d376945bf83f30ea25684c38e`.

**Assessment:** I retrieved every page quality record. The retained findings are false positives or style preferences: a valid GFM short row on page 0; equivalent code examples for different model providers on page 37; valid escaped ampersands in links and table text on pages 44, 46 and 58; link-style preferences in a valid table on page 74; `pip`/`uv` installation alternatives on page 80; valid blockquote styling on pages 84 and 93; a finding whose evidence contradicts its claim of empty table rows on page 91; and valid heading punctuation/escaped identifier syntax on page 108. No source-page repair is warranted from this run.

**Local auditor fixes applied; pending deployment:** accept GFM body rows with omitted trailing cells; discard findings that call valid escaped ampersands defective, style-only valid blockquotes or table links inconsistent, equivalent pip/uv commands duplicates, and fenced model examples redundant when only the model setting differs; validate that empty-row claims actually cite an empty row; and suppress formatting claims about valid ATX heading punctuation. The prompt now states these source-format conventions explicitly. These changes do not rewrite source content. One page remains review-required because of the transient audit timeout.

### Tier 4 — Asyncio (`docs`)

| Field | Result |
|---|---|
| Round / run | `dd-refine-2026-11-11-r2` / `61fe4eaeeaea4d52a65979efdc2bf0e4` |
| Source | `https://docs.python.org/3/library/asyncio.html` |
| Build | service `1.0.0`; Git SHA unavailable; corpus normalizer v14; agentic normalizer `dd-ingestion-agentic-v3` |
| Outcome | Failed at publish; no pages published |
| Extraction | Sphinx navigation found 66 URLs and filtered to 20; 350,860 bytes; 44,955 ms |
| Preparation | 118 agentic pages retained; no empty pages, stubs or duplicates dropped |
| Agentic stage | 900.04 seconds; all pages reached audit and normalization; publish rejected the assessment ordering |
| LLM calls | 124 calls; 7 `page_auditor` errors (1 connection error, 6 HTTP 504s) |
| Audit fallback | Page index 105 was kept unchanged after all structured-output and JSON-text recovery attempts failed |
| Error | `ValueError: page assessments must cover every source page in order` |

**Assessment:** the Sphinx path discovered and processed 20 documentation URLs into 118 agentic pages, including autodoc entity and narrative slices. The audit/normalize graph completed and preserved the page whose audit timed out. Publication failed with the same order-sensitive coverage check seen in Tier 2, confirming that this is a cross-tier publication defect rather than a source-specific issue. Failed-run staging was cleaned up, so this run has no publishable page-quality corpus to sample. It did not reach vault backfill.

**Local fix applied; pending deployment and validation:** exact unique index coverage is now validated and both assessment/output lists are reordered to source-page order before the manifest is written. To keep intermittent LLM outages from holding one page for many minutes, page audits now make at most two structured attempts with a short linear retry delay, and skip JSON-text recovery only after a transient provider error. Successful but malformed structured responses can still use JSON-text recovery; an unavailable audit continues to preserve its source page.

**Disposition:** Tier 4 extraction, preparation, audit and normalization worked; the pipeline did not complete publish or vault backfill. Tier 4 remains unvalidated until redeployment and a rerun. The Tier 2 and Tier 4 failures share the same fix and should both be rerun in the next round.

**Redeployed validation — round `dd-refine-2026-11-11-r3`:** run `e59c84dcc7cb40809ec28896cdcb575e` completed end to end with 118 pages published and vault backfill done. Extraction took 54.86s, preparation 4.54s, Agentic processing 283.95s, vault backfill 38.99s, and total runtime was 382.64s (~6m23s). The run used normalizer v14 and agentic normalizer v3. One audit (page 105) was unavailable; all source pages were preserved. Counters show 128 calls, 3 provider errors (one connection error and two HTTP 504s).

There were 9 findings on 5 pages. Sampling all affected records shows the repeated sentences are valid method-specific text under separate asyncio API entries (for example, `asyncio.run` versus `runners.run`, `WriteTransport.write` versus `DatagramTransport.sendto`, and separate Future/Task methods). The remaining formatting finding targets valid escaped underscores in a Python signature (`create\\_subprocess\\_exec`). These are auditor false positives, not content defects. I updated the local auditor prompt to explicitly preserve repeated descriptions across distinct API entries and escaped underscores in Python identifiers; that prompt change is pending deployment and validation. The durable report is `dd-benchmarks/dd-refine-2026-11-11-r3/e59c84dcc7cb40809ec28896cdcb575e.json`.

**Redeployed validation — round `dd-refine-2026-11-12-r1`:** run `8bce44df78584b04a053153991b25074` completed end to end: the Tier 4 `docs` source produced 118 pages, all 118 were normalized and published, and vault backfill completed. Extraction took 49.34s, preparation 3.57s, Agentic processing 239.34s, backfill 10.25s, and total runtime was about 302.5s. The source now identifies itself as Python 3.15.2, so comparisons with the earlier corpus are affected by upstream documentation changes. The run used normalizer v14 and `dd-ingestion-agentic-v3`.

Quality recorded 56 findings on 29 pages: 2 `duplicate_boilerplate`, 3 `duplicate_section`, 10 `malformed_heading`, and 41 `markdown_formatting`; 24 were low and 32 medium. Two audits and one repair were unavailable, leaving 3 pages review-required. No content patches were accepted or rejected. The durable report is `dd-benchmarks/dd-refine-2026-11-12-r1/8bce44df78584b04a053153991b25074.json`.

**Assessment:** sampled original and processed bodies for pages 0, 13, 33 and 69 were identical. Several findings are auditor false positives: page 0's escaped Python signature is valid; page 13's `## asyncio.create_subprocess_shell` heading already has a space; and page 69's method and attribute terms use Sphinx definition-list syntax rather than Markdown headings. Repeated descriptions cited on other pages are scoped to separate API entries. The current prompt guidance did not sufficiently constrain these cases. I added deterministic filters for valid ATX headings, escaped punctuation in Sphinx signatures, Sphinx definition-list terms and repeated text across distinct Sphinx API entries, with matching prompt guidance. These refinements are local and need redeployment plus a regression run. Provider errors and the 3 review-required pages also keep Tier 4 from converging.

### Tier 1–4 convergence status

- **Tier 1 Unsloth:** latest r1 run published all 205 pages with no audit unavailable; 3 repairs were unavailable and 4 pages remain review-required. The run exposed false-positive findings, and local auditor safeguards need deployment validation.
- **Tier 2 Optimum:** latest r1 run published all 11 pages; one audit was unavailable because of provider errors. Local source-format changes need deployment validation.
- **Tier 3 DeepAgents:** latest r1 run published all 110 pages; one audit was unavailable after an HTTP 504. All 22 retained findings were assessed as false positives or style preferences; local auditor safeguards need deployment validation.
- **Tier 4 Asyncio:** latest r1 run published all 118 pages; two audits and one repair were unavailable, leaving 3 pages review-required. The sampled findings were false positives; local prompt safeguards need deployment validation.
- **Current disposition:** the complete r1 representative pass for Tiers 1–4 succeeded operationally, including publish and vault backfill. It has not converged: provider errors left unavailable audits/repairs, and the r1 runs preceded the latest local auditor changes. The changes now cover recurring false positives across table syntax, escaped punctuation, headings, code alternatives and source styling. After a manual redeploy, rerun all four representatives in order to confirm the shared auditor changes and reassess provider availability. Tier 5 remains out of scope.

## Refinement round `dd-refine-2026-11-13-r1`

The deployed build reported corpus normalizer v17. The Tier 1 Unsloth benchmark (`19e1356c6f7f43dc9b6932772e2a8525`) selected the expected `llms_full` source and downloaded 3,249,260 bytes in 50.43 seconds, but terminated after 71.46 seconds with `NameError: name 'INDENTED_LABEL_RE' is not defined`. Agentic auditing, normalization, publishing and vault backfill did not start. No visible framework corpus was changed; the failed run and its isolated storage slug are recorded at `dd-benchmarks/dd-refine-2026-11-13-r1/19e1356c6f7f43dc9b6932772e2a8525.json`.

**Root cause and local fix:** the v17 corpus-normalization pass referenced `INDENTED_LABEL_RE` and `INDENTED_DESCRIPTION_RE` as globals, although both patterns are defined in the imported `patterns` module. All four references in `_indented_label_description_pass` now use `patterns.*`. The normalizer version is v18 so existing corpora re-run the intended v17 list repair during backfill.

**Validation:** the isolated indented-label pass smoke check passed for three label/description pairs, and the module source compiled. Importing the full normalizer package in the host Python environment was unavailable because `langchain` is not installed there. The deployed benchmark exposed this shared preprocessing failure before tier-specific extraction output could reach Agentic AI. Tiers 2–4 were therefore not run against the same broken build, since they would fail in the same normalizer pass without providing useful tier coverage.

**Disposition:** v18 is local and pending redeployment. After the manual redeploy, start a fresh round (do not reuse r1) and rerun Tier 1 through Tier 4 in order; confirm normalization, Agentic completion, publish and vault backfill for every tier before assessing convergence. Tier 5 remains out of scope.

## Benchmark acceptance record

For every run, record:

- code SHA, round ID, framework, requested tier and source URL;
- source attempt outcome and extraction page/byte counts;
- post-processing input/output counts and drops;
- Agentic AI pages, audit/repair availability, issue categories, accepted/rejected patches, review-required pages and deterministic fixes;
- phase timings and vault/publish outcome;
- sampled content checks and any regression found.

Compare runs within a round before attributing changes to code. Compare different rounds only when the source and meaningful upstream content are unchanged; otherwise treat the upstream change as a confounder.
