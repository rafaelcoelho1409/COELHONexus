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

**Disposition:** the baseline pipeline completed, but Tier 1 has not converged. The fixes above need redeployment and a regression run. Tier 2–4 results are tracked below as they are run. Tier 5 remains out of scope.

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

**Disposition:** extraction, normalization, publish and vault backfill succeeded, but Tier 3 has not converged because one page was unaudited and the page-title and auditor false-positive fixes still need a deployed regression run. The durable report remains under `dd-benchmarks/dd-refine-2026-11-11-r2/`.

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

### Tier 2–4 convergence status

- **Tier 2 Optimum:** extraction and preparation succeeded; publish failed on assessment ordering.
- **Tier 3 DeepAgents:** full pipeline succeeded; one page's audit was unavailable, and findings exposed page-title and auditor false-positive fixes.
- **Tier 4 Asyncio:** extraction through normalization succeeded; publish failed on the same assessment-ordering defect as Tier 2.
- **Current disposition:** no tier is considered converged. Tier 3 completed successfully on this build, but shared auditor and title fixes remain unvalidated. Deploy the accumulated changes, then rerun Tiers 2–4 sequentially; Tier 1 also needs the normalizer-v14/v3 regression run documented above.

## Benchmark acceptance record

For every run, record:

- code SHA, round ID, framework, requested tier and source URL;
- source attempt outcome and extraction page/byte counts;
- post-processing input/output counts and drops;
- Agentic AI pages, audit/repair availability, issue categories, accepted/rejected patches, review-required pages and deterministic fixes;
- phase timings and vault/publish outcome;
- sampled content checks and any regression found.

Compare runs within a round before attributing changes to code. Compare different rounds only when the source and meaningful upstream content are unchanged; otherwise treat the upstream change as a confounder.
