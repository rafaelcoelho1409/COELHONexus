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

1. Choose one representative source for each tier and note the current code revision.
2. Start one benchmark at a time. Keep using the same `round_id` for runs against the same code revision; use a new round after code changes and redeployment.
3. Follow the run through the regular ingestion progress route. Its response includes a `benchmark` object for benchmark runs.
4. Read the round report after each run. Review failures, source attempts, page counts, timing, quality metrics and stored pages.
5. Assess a small sample of extracted and normalized pages for content fidelity, links, code examples, navigation order and missing sections. Quality counters alone do not establish correctness.
6. Fix issues, record the change, and wait for the user to redeploy Skaffold. Repeat with a new round ID, then rerun previously passing representatives as regression checks.
7. Consider a tier ready when its representative run completes without extraction or publish errors, expected content is present, Agentic AI completes without unavailable pages, vault backfill succeeds, and sampled output preserves the source's teaching content. Record any accepted review items and known source-specific limitations.

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
| 2 — `llms_txt` | Optimum (~16 pages) | React (~180 pages) | Index parsing, page ordering, section metadata and JSX residue |
| 3 — `sitemap` | DeepAgents (~73 pages) | FastAPI (~153 pages) | Sitemap filtering, URL extraction and a second documentation-site generator |
| 4 — `docs` | Asyncio (~73 slices) | Celery for Python (~234 pages) | Sphinx inventory/toctree traversal, fragment handling and autodoc-heavy pages |

These are candidate sources, not a claim that they are the only or always-best corpus. Confirm that the selected catalog entry has the requested tier URL before each run. Tier 1's banner issue and Tier 4's Sphinx edge cases are included intentionally so the refinement loop exercises known risk areas.

## Benchmark acceptance record

For every run, record:

- code SHA, round ID, framework, requested tier and source URL;
- source attempt outcome and extraction page/byte counts;
- post-processing input/output counts and drops;
- Agentic AI pages, audit/repair availability, issue categories, accepted/rejected patches, review-required pages and deterministic fixes;
- phase timings and vault/publish outcome;
- sampled content checks and any regression found.

Compare runs within a round before attributing changes to code. Compare different rounds only when the source and meaningful upstream content are unchanged; otherwise treat the upstream change as a confounder.
