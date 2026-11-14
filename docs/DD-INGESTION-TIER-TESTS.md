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

## Refinement round `dd-refine-2026-11-13-r2`

This round used the manually redeployed build, which reports corpus normalizer v18 and `dd-ingestion-agentic-v3`. Its reported Git SHA is blank. The four representative tiers are being run sequentially in isolated benchmark storage; Tier 5 remains excluded.

### Tier 1 — Unsloth (`llms_full`)

Run `b556d6766fc14eb1b515294f5481265b` selected the intended source, downloaded 3,249,260 bytes and split it into 205 pages. Preparation produced 205 non-empty pages. Audit, normalization, publish and vault backfill all completed; all 205 pages were published. Timings were 48.69s extraction, 22.58s preparation, 1,278.44s Agentic processing, 21.67s backfill and 1,371.53s total (~22m52s).

Quality reported 143 findings on 64 pages: 29 duplicate-boilerplate, 50 duplicate-section, 1 encoding, 5 malformed-heading, 48 Markdown-formatting and 10 other. It recorded 47 low- and 96 medium-severity findings. Four audits were unavailable (pages 48, 55, 114 and 166); repair was unavailable for pages 46 and 169; 6 pages require review. The 241 LLM calls used 1,938,412 input and 160,675 output tokens; 28 auditor requests returned HTTP 504. Page 114's audit error was a 504. Pages 48, 55 and 166 returned incompatible JSON shapes and could not be validated as `PageAssessment`; those source pages were retained without an audit. Repair made 20 calls with no provider errors.

**Sample assessment and local fixes:** page 46's reported solitary `!` is the valid image marker inside a `<figure>![](...)` block, so the finding is a false positive and its failed repair should not require review. A narrow sanitizer rule now drops that exact claim when the image syntax is present. Page 169 has a real heading export defect: `### &#x20;:mag\\_right:Training ...`; the HTML entity denotes a space, while the escaped shortcode underscore and missing separator leave the icon text glued to “Training.” I updated the corpus normalizer to recognize this export form, restore the shortcode underscore, and insert the heading separator. These fixes are local and need redeployment; the three unavailable structured audits remain open.

The durable report is `dd-benchmarks/dd-refine-2026-11-13-r2/b556d6766fc14eb1b515294f5481265b.json`.

### Tier 2 — Optimum (`llms_txt`)

Run `b5df2db0b1f746a0ac2929bc67a624a5` selected `https://huggingface.co/docs/optimum/llms.txt`, extracted 11 pages / 86,659 bytes, prepared 11 pages without drops, completed audit, normalization, publish and vault backfill, and published all 11 pages. Timings were 1.86s extraction, 1.03s preparation, 55.36s Agentic processing, 1.41s backfill and 59.79s total. All 11 auditor calls succeeded; there were no audit or repair unavailability, rejected patches or review-required pages. Quality reported 9 findings across pages 0, 5, 6 and 10 (3 duplicate-section and 6 Markdown-formatting; 5 low and 4 medium); no repair patch was accepted. The durable report is `dd-benchmarks/dd-refine-2026-11-13-r2/b5df2db0b1f746a0ac2929bc67a624a5.json`.

**Sample assessment and local fix:** pages 0, 6 and 10 retain genuine Hugging Face API-export concatenation, including a stray `0` joining parameter/return descriptions and adjacent prose. The existing exact Optimum repair handled four entries but missed these contexts, so the specialized pass needs a broader, still evidence-bounded repair. Page 5's findings about code styling and spacing are not defects; one description claims backticks that are absent from its evidence. Page 6's ``bool`` return type is valid Markdown. Page 10's repeated parameter and explanatory text occurs under separate transformation classes, so those duplicate findings are false positives. A sanitizer rule now rejects repeated evidence when each occurrence is scoped under a distinct source-linked API heading. This safeguard is local and must be redeployed; the Optimum export fragments still need a narrowly safe content repair.

### Tier 3 — DeepAgents (`sitemap`)

Run `72962397567f43ed89152b7cea753c99` selected the catalog sitemap (70 URLs / 2,029,530 bytes), split those URLs into 110 non-empty pages, and completed audit, normalization, publish and vault backfill. All 110 pages were published. Timings were 30.59s extraction, 16.90s preparation, 405.02s Agentic processing, 12.17s backfill and 464.87s total (~7m45s).

Quality reported 26 findings on 17 pages: 1 duplicate-boilerplate, 2 duplicate-section, 3 malformed-heading, 19 Markdown-formatting and 1 other; 12 were low and 14 medium severity. Page 65's audit was unavailable and remains review-required. The 123 auditor calls used 1,026,397 input and 55,990 output tokens; 8 calls returned HTTP 504. No repair calls or patches were recorded. The durable report is `dd-benchmarks/dd-refine-2026-11-13-r2/72962397567f43ed89152b7cea753c99.json`.

**Sample assessment and local fixes:** page 30's four rows beginning `\\|` immediately follow a valid table header and delimiter; they are intended table rows but render as escaped prose. A normalizer pass now removes the first escape only when a real table header/delimiter and compatible row width establish the context. This is local v20 and needs deployment. Page 27 contains upstream typos (“agenet”, “Agnets”, “laets”); these are editorial source defects, so the ingestion repairer correctly does not silently rewrite them. Page 33 repeats a long paragraph prefix immediately before its complete version; the sanitizer now identifies this as a duplicate section, and duplicate deletion is enabled only through the existing exact-source, confidence, unique-target and surviving-copy gates. Page 84's repeated “usage example” link is under different integration headings and is contextual, not page furniture. Additional local audit filters reject verified false positives from valid table rows, wrapped list items, normal spacing between code examples, configured-model variants, shared integration links and curated/all-items table overlap. Other sampled findings were false positives or diagram/export labels: valid block-wrapped table rows, provider-specific rubric examples, intentional model listings, concise headings, and a migration prompt supplied as documentation. Page 65 returned the audit request envelope instead of a `PageAssessment`; the extracted source was preserved.

### Tier 4 — Asyncio (`docs`)

Run `71aac097a89b406c97f97e47dc743d6e` selected `https://docs.python.org/3/library/asyncio.html`, extracted 118 pages / 350,860 bytes from the 20-page documentation tree, and prepared all 118 without splitting or drops. Audit, normalization, publish and vault backfill completed; all 118 pages were published. Timings were 51.61s extraction, 3.92s preparation, 200.86s Agentic processing, 11.75s backfill and 268.30s total (~4m28s).

Quality reported 45 findings across 22 pages: 1 duplicate-boilerplate, 13 duplicate-section, 4 malformed-heading, 26 Markdown-formatting and 1 other; 23 were low and 22 medium severity. Every audit completed; there were no unavailable repairs, rejected patches or review-required pages. The 125 auditor calls used 544,315 input and 31,372 output tokens; 3 calls returned HTTP 504 and recovered. The durable report is `dd-benchmarks/dd-refine-2026-11-13-r2/71aac097a89b406c97f97e47dc743d6e.json`.

**Sample assessment and local fixes:** most findings concern valid escaped Python identifiers, Sphinx definition-list signatures/descriptions, or shared text under distinct methods. Examples include repeated `Future`/`Task` behavior across separate methods, the `WriteTransport.write()`/`writelines()` explanations, and `asyncio.run()` versus `Runner.run()` descriptions. These are contextual API documentation, not repeated blocks within one entry. The local audit sanitizer now recognizes colon-prefixed Sphinx API definition lists, accepts valid heading evidence and escaped identifiers, scopes duplicate checks to Sphinx signature entries, and drops a parameter-name style claim unsupported by an ingestion defect. High-confidence exact duplicate repair is enabled behind the surviving-copy and unique-target validation gate. No deterministic Tier 4 source transformation was indicated; its Sphinx term/definition structure and content were preserved.

### Round disposition

All four representative tiers completed operationally, including publication and vault backfill. Tier 2 and Tier 4 had no unavailable pages or review-required records. Tier 1 had four unavailable audits and two repairs; Tier 3 had one unavailable audit. Tier 1's auditor returned 28 provider 504s, Tier 3 had 8, and Tier 4 had 3 recovered 504s. Local v19 and v20 address sampled Unsloth heading and DeepAgents table defects. The local audit changes suppress source-context false positives, retain editorial source typos for review, and allow high-confidence duplicate repair only under exact evidence and surviving-copy guards. These changes were not part of the deployed v18 round. A manual redeploy and a fresh Tiers 1–4 pass are required to validate them and establish convergence. Tier 5 remains out of scope.

## Benchmark acceptance record

For every run, record:

- code SHA, round ID, framework, requested tier and source URL;
- source attempt outcome and extraction page/byte counts;
- post-processing input/output counts and drops;
- Agentic AI pages, audit/repair availability, issue categories, accepted/rejected patches, review-required pages and deterministic fixes;
- phase timings and vault/publish outcome;
- sampled content checks and any regression found.

Compare runs within a round before attributing changes to code. Compare different rounds only when the source and meaningful upstream content are unchanged; otherwise treat the upstream change as a confounder.

## Refinement round `dd-refine-2026-11-13-r3` — preflight stopped

After the reported redeploy, the first isolated Unsloth benchmark (`931926bfdd734993b2a859d8e9ef7711`) returned `normalizer_version: 18`, while the workspace build is at v20. The run downloaded the 3,249,260-byte source bundle, then was canceled before preparation or Agentic processing; no pages were published and no catalog corpus was changed. The canceled report remains at `dd-benchmarks/dd-refine-2026-11-13-r3/931926bfdd734993b2a859d8e9ef7711.json`.

**Disposition:** this is a stale-build preflight, not a validation of the local v19/v20 fixes. Do not run Tiers 1–4 again until the server reports normalizer v20. Then use a new round ID and continue sequentially. Tier 5 remains out of scope.

## Refinement round `dd-refine-2026-11-13-r4`

The manually redeployed server reported corpus normalizer v20 and `dd-ingestion-agentic-v3`. The v20 Unsloth heading and Tier 1 audit-filter changes were exercised first, followed by Tiers 2–4. Tier 5 remains excluded.

### Tier 1 — Unsloth (`llms_full`)

Run `2804ecebc23f49fdaacb36e2e87117eb` extracted the 3,249,260-byte bundle and prepared 205 pages, dropping one empty split. Audit, normalization, publish and vault backfill completed; all 205 pages were published. Timings were 50.33s extraction, 27.36s preparation, 1,577.70s Agentic processing and 22.21s vault backfill. The source bundle matched the prior r2 run.

Quality reported 151 findings across 57 pages: 32 duplicate-boilerplate, 88 duplicate-section, 1 encoding, 1 malformed-heading, 28 Markdown-formatting and 1 other; 35 were low, 115 medium and 1 high severity. One audit (page 52) was unavailable after the structured response returned non-JSON text. Repair was unavailable on 20 pages, leaving 21 review-required pages. The run made 221 audit and 174 repair calls; provider counters recorded 28 HTTP 504 errors total (22 audit, 6 repair). No patches were accepted or rejected. The durable report is `dd-benchmarks/dd-refine-2026-11-13-r4/2804ecebc23f49fdaacb36e2e87117eb.json`.

**v20 regression checks:** page 169's exported `&#x20;:mag\\_right:Training` heading now normalizes to `### :mag_right: Training ...`, and its quality record has no retained issues; the page recorded one emoji-heading spacing fix. Page 46's figure-contained image marker also has no retained issues. This validates the targeted v19 heading repair and false-positive filter. Compared with r2, audit unavailability fell from four pages to one and findings were concentrated on fewer pages (57 vs 64), but repair unavailability rose from two to 20 and review-required pages from six to 21. Pages 38, 55 and 71 retain repeated blocks under separate dated updates/model variants; page 203 repeats notebook instructions and figures across distinct tutorials. These are context-scoped source repeats, not safe deletion targets. A local sanitizer now recognizes those explicit update, model-variant and tutorial scopes, and the auditor prompt has matching guidance; both are pending deployment. Provider and structured-output reliability still needs review. Tier 1 is operationally successful but not converged.

### Tier 2 — Optimum (`llms_txt`)

Run `80b5d24e92b34c7998a5a78f0bd1bd0a` extracted 11 pages / 86,659 bytes, prepared all 11 without splitting or drops, and completed audit, normalization, publish and vault backfill. All 11 pages were published. Timings were 4.53s extraction, 2.00s preparation, 224.35s Agentic processing and 1.27s vault backfill (~3m52s total).

Quality reported 5 findings across pages 4, 8 and 10: 3 duplicate-section and 2 Markdown-formatting findings (2 low, 3 medium). No audit was unavailable; repair was unavailable on pages 4 and 10, leaving 2 review-required pages. The 30 LLM calls recorded one HTTP 504 (13 audit calls and 17 repair calls); no patch was accepted or rejected. Four API entries were mechanically restored and ten indented label/description groups converted to lists. The durable report is `dd-benchmarks/dd-refine-2026-11-13-r4/80b5d24e92b34c7998a5a78f0bd1bd0a.json`.

**Assessment and local v22 fixes:** pages 0 and 6 now have no retained findings. Page 4's two remaining duplicate claims are the `library_name` parameter repeated under the same `get_model_from_task` heading, so they may be genuine duplication within one API entry; the other scoped API repeats were correctly discarded. Page 10's repeated `preserves_computation` parameter belongs to distinct transformation classes. The source-linked API scope now includes ancestor headings, so class-specific `__call__` entries can be distinguished without suppressing repetition inside one class. Page 10 also retains a real export concatenation: `...only once.0`torch.fx.GraphModule`The transformed module.` A new v22 normalizer pass restores the missing Returns/Parameters boundary only for this exact Optimum transformation signature. These changes need redeployment and regression; Tier 2 is not yet converged.

### Tier 3 — DeepAgents (`sitemap`)

Run `b50c45ae50e44eaeb581922213da3bb5` selected the catalog sitemap (70 URLs / 2,029,530 bytes), split them into 110 pages, and completed audit, normalization, publishing and vault backfill. All 110 pages were published. Timings were 41.33s extraction, 25.14s preparation, 609.31s Agentic processing and 16.64s backfill (~11m32s total).

Quality reported 11 `markdown_formatting` findings across 9 pages (7 low, 4 medium). All 110 audits and repairs completed, no page required review, and no patches were accepted or rejected. The 130 LLM calls recorded eight HTTP 504s, all recovered sufficiently for the run to finish. The durable report is `dd-benchmarks/dd-refine-2026-11-13-r4/b50c45ae50e44eaeb581922213da3bb5.json`.

**v20 regression check and local follow-up:** page 65, whose prior audit returned its request envelope, now completed with no retained issue; a repeated-code finding was correctly discarded because its evidence is in a fenced example. Page 30's four escaped table rows remain unchanged and are still reported as a formatting issue. Inspection showed one blank line between the preceding recognized table and the escaped row run, so v20's contiguous-row rule never reached them. The local v21 table repair, now included in version v22, removes that separator and unescapes the rows only when at least two consecutive candidate rows match the recognized table width. This fix is pending deployment and regression. Tier 3's audit/review status is much better than r2 (zero unavailable/review pages versus one each), but the table defect means it is not yet converged.

### Tier 4 — Asyncio (`docs`)

Run `6c75919a31ef43ce8c7fed37b1171a27` selected the Python asyncio documentation tree, extracted 118 pages / 350,860 bytes, retained all 118 during preparation, and completed audit, normalization, publish and vault backfill. All 118 pages were published. Timings were 66.69s extraction, 8.73s preparation, 359.55s Agentic processing and 19.12s vault backfill (~7m34s total).

Quality reported 16 findings across 12 pages: 1 encoding, 3 malformed-heading and 12 Markdown-formatting; 12 were low and 4 medium severity. Two audits (pages 0 and 30) were unavailable after returning non-JSON responses; no repairs were unavailable, 2 pages require review, and 2 patches were accepted. The 128 LLM calls recorded 2 HTTP 504s. The durable report is `dd-benchmarks/dd-refine-2026-11-13-r4/6c75919a31ef43ce8c7fed37b1171a27.json`.

**Assessment and local audit fixes:** the previously noisy asyncio pages 13, 33 and 69 now have no retained findings, validating the heading, Sphinx definition-list and scoped-repeat filters. Page 40 still has two style-only findings: escaped asterisks in a valid signature and bold text inside a valid blockquote described as “unusual.” The sanitizer now drops those narrow syntax/style claims. It also scopes duplicate findings to distinct dated GitBook updates, model variants and numbered tutorials, preserving repeated source content where each section needs its own instructions or examples. The two unavailable audits returned 43 and 14,534 characters of non-JSON text rather than a `PageAssessment`; their pages were retained unchanged. R4 has substantially fewer findings than the previous v18 run (16 vs 45), but the two structured-output fallbacks still need a deployed regression.

### Round `dd-refine-2026-11-13-r4` disposition

All four tiers completed extraction, Agentic processing, publishing and vault backfill on deployed normalizer v20. Tier 1 published all 205 pages, validated the heading repair, but has 21 review-required pages after provider and repair-output failures. Tier 2 published all 11 pages with no unavailable audits, but retains the exact Optimum export concatenation until v22 is deployed. Tier 3 published all 110 pages with no unavailable/review pages; its escaped table continuation needs the v21/v22 rejoin rule. Tier 4 published all 118 pages and reduced retained findings from 45 to 16, but two structured audits were unavailable. Local v21/v22 normalizer and auditor changes are pending redeployment. No tier is converged on this round; Tier 3 and Tier 4 are closest operationally. After manual redeploy, start a new round and rerun Tier 1–4 sequentially to validate shared fixes. Tier 5 remains excluded.

## Refinement round `dd-refine-2026-11-14-r1` — full-tier baseline

The deployed service reported corpus normalizer v25, agentic normalizer v3 and build fingerprint `ccdd8f9d9d9eb80971dc41cf92ef854f5c997b0a045eb57b0f4cabe9e71fbde7`. Git SHA was blank. This round ran the four representative tiers sequentially in isolated benchmark storage. Every run completed extraction, preparation, Agentic processing, publish and vault backfill; all prepared pages were published. Tier 5 was not run.

| Tier | Source and run | Coverage and timing | Agentic result |
|---|---|---|---|
| 1 — Unsloth (`llms_full`) | `e411da6f451849d7b2a10d809a4efc46`; source snapshot `993a75db58d87df297afe8e86aa0cefc355a1338bffb393811a92efa09e0583b` | 3,249,260 source bytes; 205 prepared/published pages; one empty split dropped. Total 1,740.29s (~29m). | 119 findings on 61 pages: 9 duplicate boilerplate, 74 duplicate sections, 3 encoding artifacts, 1 malformed heading, 29 Markdown formatting, 3 other. 36 low, 83 medium. One audit unavailable (page 71), repair unavailable on 13 pages, 15 review-required pages. One patch accepted and one rejected. 329 calls, 46 provider 504s (42 audit, 4 repair). |
| 2 — Optimum (`llms_txt`) | `9565357d79b14ff8aa97444332b0407b`; source snapshot `c498ea9dbf2d87cb9308aa13a6565cf7fe661567b2a46a3ebc2b33e98911d367` | 11 source/prepared/published pages; no drops. Total 139.66s (~2m20s). | Zero findings, unavailable audits/repairs, accepted/rejected patches or review-required pages. 12 calls; one 504 was recorded and the run completed. |
| 3 — DeepAgents (`sitemap`) | `286525542be1476ba744cee8fd89a757`; source snapshot `a87fcf640d904402d89a0436d832ebc30fc58ed7b71c85c3920bf6f425115d88` | 70 sitemap sources / 2,029,530 bytes became 110 prepared/published pages; no drops. Total 629.44s (~10m29s). | Six findings on five pages: 2 duplicate sections, 3 Markdown formatting, 1 other; 5 low and 1 medium. Audit unavailable on page 45; no repair unavailable; two pages require review; zero accepted and one rejected patch. 127 calls, five provider 504s. |
| 4 — Asyncio (`docs`) | `ace73b9c0c4349b794431f4e9d6c25ae`; source snapshot `6308177d907b68c09a15c9ad1a7cc59cca2029e583f66b2e1e6e62923e1614aa` | 20 captured source files / 350,860 extracted bytes became 118 prepared/published pages; no drops. Total 292.46s (~4m52s). | One low-severity Markdown-formatting finding. Audits unavailable on pages 4, 28 and 63; no repair unavailable; three pages require review. 124 calls, one provider 504. |

The report totals and phase timings are stored by the benchmark API under `dd-benchmarks/dd-refine-2026-11-14-r1/`. All four tiers completed operationally, but none is converged: Tier 1 has substantial unresolved review/provider failures; Tier 3 has findings and an unavailable audit; Tier 4 has three unavailable audits; Tier 2's clean full run still needs the same-build fixed-input rubric disposition before acceptance. These results are a baseline for the deployed v25 build, not validation of the later local auditor/prompt changes.

## Refinement round `dd-refine-2026-11-14-r2` — deployment preflight stopped

After the user reported a redeploy, `GET /api/v1/dd/debug/provenance` still returned the r1 fingerprint `ccdd8f9d9d9eb80971dc41cf92ef854f5c997b0a045eb57b0f4cabe9e71fbde7`, auditor hash `dc710fb3a3bffe121420ae3a4da0943287fa058725a63ee533544165df3a0b8a`, and page-auditor/page-repair prompt hashes `c3ceea58b40ca3545aa7d443ddd167d5a39dbeb6db15f506502f1c29775beef0` / `66c4915226de6bcdbfccd4304b0114ffac129fdb3fb9136ac73d1d87f3ccbbe2`. The workspace computes fingerprint `16121696f0bb64630ded9769491990f336348e6171842990b4cf5e289d361b2c`, auditor hash `4741105f7659675c13900ebb6acda236be05962158ce058881adc67202fa2f8f`, and prompt hashes `f087eed750158911c23597230fccc96a240b29542c1782f804868597e724e6c4` / `9f964e8a93612834edee4f26f53ba695cffd6fdac04a320aaad06cb99156a117`. Because the deployed auditor and prompt hashes are unchanged, no new full-tier jobs were launched against this build; doing so would repeat r1 and would not validate the local fixes.

The local checks `scripts/server/test_dd_page_agents_retry.py` and `scripts/server/test_dd_ingestion_evaluation.py` both passed, including source-preserving audit-outage behavior. After redeployment, confirm the API returns the workspace auditor and prompt hashes (and a changed fingerprint), then run a fresh sequential Tier 1–4 round. Tier 5 remains out of scope.

## Refinement round `dd-refine-2026-11-14-r3` — full tiers 1–4

After redeployment, provenance matched the workspace build: fingerprint `16121696f0bb64630ded9769491990f336348e6171842990b4cf5e289d361b2c`; auditor hash `4741105f7659675c13900ebb6acda236be05962158ce058881adc67202fa2f8f`; auditor and repair prompt hashes `f087eed750158911c23597230fccc96a240b29542c1782f804868597e724e6c4` and `9f964e8a93612834edee4f26f53ba695cffd6fdac04a320aaad06cb99156a117`; corpus normalizer v25 and Agentic normalizer v3. The four benchmark runs were sequential, and all reached publish and vault backfill. Tier 5 was excluded.

| Tier | Run | Coverage and timing | Agentic result |
|---|---|---|---|
| 1 — Unsloth (`llms_full`) | `e599457f8f984058a2274318362cb53e`; snapshot `993a75db58d87df297afe8e86aa0cefc355a1338bffb393811a92efa09e0583b` | 3,249,260 source bytes; 205 prepared/published pages; one empty split dropped; 1,484.75s total (~24m45s). | 103 findings across 47 pages: 18 duplicate-boilerplate, 55 duplicate-section, 1 malformed-heading, 28 Markdown-formatting, 1 other. 30 low and 73 medium. No unavailable audits; repair unavailable on page 22; one page requires review. 225 calls, 31 provider errors; no patches accepted/rejected. |
| 2 — Optimum (`llms_txt`) | `89b31b242c2e4272972a115d4686d6de`; snapshot `c498ea9dbf2d87cb9308aa13a6565cf7fe661567b2a46a3ebc2b33e98911d367` | 11 source/prepared/published pages; no drops; 60.77s total. | One medium duplicate-section finding on page 0; no unavailable audits/repairs or review pages. 11 calls, zero errors; no patches accepted/rejected. |
| 3 — DeepAgents (`sitemap`) | `3324037265224d729cc08baf0355a013`; snapshot `a87fcf640d904402d89a0436d832ebc30fc58ed7b71c85c3920bf6f425115d88` | 70 sitemap sources / 2,029,530 bytes; 110 prepared/published pages; no drops; 702.26s total (~11m42s). | 13 findings across 9 pages: 1 duplicate-boilerplate, 11 Markdown-formatting, 1 other; 8 low and 5 medium. No unavailable stages or review pages. 126 calls, 15 provider errors; no patches accepted/rejected. Four escaped table continuation pipes were normalized. |
| 4 — Asyncio (`docs`) | `84a14914693e4d24bc94034e23aac1fa`; snapshot `421532de2035c079ee91524a6a20c24b08f4a973d99a7fefb1d5e199a2f0ba8f` | 118 source/prepared/published pages / 350,860 extracted bytes; no drops; 245.94s total (~4m6s). | Six Markdown-formatting findings across 5 pages; 1 low and 5 medium. Audits unavailable on pages 49 and 96; two pages require review. 123 calls, one provider error; no patches accepted/rejected. |

### Review and follow-up

- **Tier 1:** page 22's optional repair failed to return structured output. Its source and processed body hashes match; the normalizer did not remove or rewrite its literal backslashes. Keep the source intact and treat its one review record as inconclusive, not a successful repair. The provider error count and 103 remaining findings mean Tier 1 is not converged.
- **Tier 2:** the one repeated `library_name` mention is present in the original API reference and refers to distinct method parameters. Preserve the source; do not filter it automatically unless its document structure can be reliably scoped to distinct API entries. Tier 2 is operationally strongest, but it is not converged until the fixed-input rubric explicitly dispositions this finding.
- **Tier 3:** sampled source checks found upstream editorial typos and repeated card text, which should remain source-faithful; valid table/export constructs were also among the findings. Page 80's three claimed broken links are confirmed false positives: the cited Browserbase, session-recording, Search and Fetch links all have complete Markdown label and destination syntax in the exact original source. The new fixture preserves this frozen input and forbids those cited valid links from reappearing as evidence. The full run had no unavailable or review-required pages, but 13 findings and 15 provider errors remain.
- **Tier 4:** checked findings describe normal Sphinx wrapped prose, a valid italicized default parameter in a signature, contextual repetition across API methods, and an escaped identifier absent from the cited evidence. Pages 49 and 96 had unusable model responses; source and processed hashes match on inspected cases. The current-run samples motivate the local generic evidence/context filters below; until replayed on the deployed build, the six findings and two unavailable audits remain the recorded outcome.

The workspace now has narrower auditor-sanitizer checks: recognize ordinary wrapped prose inside Sphinx definition bodies; allow valid `*parameter=default*` signature notation; suppress repeated explanations only when their occurrences are under distinct Sphinx API terms; reject a source-character claim when its escaped, emphasized identifier is absent from the exact cited evidence; and discard a missing-bracket claim only when every cited bracketed label forms a complete inline link. Regressions keep same-entry repetition and genuinely broken links reviewable. These checks do not edit source text. Both offline checks pass: `test_dd_page_agents_retry.py` (including the new cases) and `test_dd_ingestion_evaluation.py`.

This is an operational full-tier pass, not convergence. The frozen-input fixtures now cover DeepAgents page 80 and Asyncio pages 0, 4, 42, 54 and 99 from their r3 runs. After manual redeployment, run these targeted non-publishing replays under a fresh evaluation round, verify source/output hashes and issue disposition, and then decide whether the remaining provider and quality signals justify another full sequential Tier 1–4 run. No source-specific cleanup or Tier 5 run is justified by this evidence.

## Targeted replay round `dd-replay-2026-11-14-r3`

The user redeployed the matching build (fingerprint `9379ea0eed7d478dfacbf0cb18a2a420110eab022a0789e975bda06ec5be6cc3`; corpus normalizer v25; Agentic normalizer v3). Both replays were fixed-input, sequential, and `publishes: false`.

| Source tier | Replay | Result |
|---|---|---|
| 3 — DeepAgents page 80 | `7011297ebb01475caf638940951badcc` from full run `3324037265224d729cc08baf0355a013` | Done in 3.30s. One audit call, zero provider errors; zero findings, unavailable audits/repairs, accepted repairs or failed checks. All three cited valid links were absent from findings. Source, normalized input and output SHA-256 all matched `e4ee16c1af532f3cef0d27dcba606993eb5d9f5e501a744cf3a6d62fc419b12e`. |
| 4 — Asyncio pages 0, 4, 42, 54, 99 | `d27910ca659a4d4c8393cf5e939acb1c` from full run `84a14914693e4d24bc94034e23aac1fa` | Done in 38.89s. Five audit calls, zero provider errors; zero findings, unavailable audits/repairs, accepted repairs or failed checks. All five flagged examples were absent from findings, and each page's input/output hash matched its frozen source hash. The combined source snapshot is `88a88153c9a57c6d67e26a89c40d59099620952efc52bc8d1ff4f075c73ac577`. |

The deterministic gates passed for both replays. Manual review confirms the fixed cases are valid source constructs or evidence mismatches and that no page text was changed by the auditor/repairer. The replay reports continue to set `human_review_required` because each fixture has an explicit rubric; this document records the manual disposition. This validates the targeted cases, not full-tier convergence: the prior Tier 1–4 run still has its provider-error and review signals, and the other pages' quality distributions were not re-evaluated. A new full-tier run is not needed just to validate these exact fixes; revisit it after resolving the remaining tier-wide quality/reliability signals.

## Targeted assessment and follow-up — local changes pending redeploy

Read-only page-quality records from full round r3 refined the remaining findings. The live endpoint stopped accepting connections before new replay jobs were queued, so no replay or ingestion was started in this follow-up.

- **Tier 1 — Unsloth:** page 22's optional patch attempt ended without a structured `PagePatchBatch`; its source remained intact. The auditor recorded 31 transient HTTP 504 attempts across the tier, but no page audit was ultimately unavailable. Page 197 repeats installation notes under separate `uv` and `conda/mamba` workflows. A generic heading-scoped rule now preserves repeats across distinct install choices. The repairer gets one schema-feedback retry; its patch gate and source-retention behavior are unchanged.
- **Tier 2 — Optimum:** the single `library_name` repeat is source-backed and occurs in distinct parameter contexts. A fixture now records the accepted disposition: preserve both occurrences and accept no automatic repair. A successful targeted replay will make this tier eligible for convergence because the full 11-page run had no unavailable stages or provider errors.
- **Tier 3 — DeepAgents:** page 80's three false broken-link findings were already absent in the successful targeted replay. The other recorded issues are: three source typos on page 27; a partially repeated card teaser on page 41 whose intent remains a human decision; valid empty cells on page 70; a valid plain-text name in a linked table on page 71; a GitHub owner spelling on page 74 that cannot be verified from the page; `N/A` table values on pages 90 and 104; and a source grammar issue on page 105. A generic sanitizer now drops speculative empty-cell, `N/A`-as-formatting, and link-style findings only when the cited evidence is in a valid GFM row. It leaves editorial/source questions visible and does not edit page text.
- **Tier 4 — Asyncio:** page 49's audit response omitted a confidence score; page 96 returned non-JSON text. Missing audit confidence now defaults conservatively to zero, and malformed responses receive a bounded schema-specific retry. This does not raise repair confidence or authorize a patch. The source remains unchanged whenever audit/repair output is unavailable.

The targeted replay fixtures now cover all previously flagged Tier 3 pages, the two Tier 4 unavailable pages plus the five already reviewed Tier 4 pages, Tier 1 pages 22/197, and Tier 2 page 0. The evaluator can assert repair status and reject specific false-positive descriptions; provenance now hashes the shared schemas/service, page-agent configuration, retry bounds and patch-gate code so deployment checks detect these changes.

After manual redeployment and provenance confirmation, run the four fixed-input replay jobs sequentially through the DD single-flight gate: Optimum `[0]`; Asyncio `[0, 4, 42, 49, 54, 96, 99]`; DeepAgents `[27, 41, 70, 71, 74, 80, 90, 104, 105]`; and Unsloth `[22, 197]`. Compare source/input/output hashes and inspect any accepted patch. Tier 2, 3 and 4 can be considered converged if their gates pass, every remaining source finding receives the documented human disposition, and no new unavailable stage or content change appears. Tier 1 still needs a broader assessment of its remaining finding pages and recovered provider errors. Tier 5 remains excluded.
