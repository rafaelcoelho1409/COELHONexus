# Agentic AI refinement playbook

**Purpose:** improve an agentic workflow with short, attributable evaluation cycles while preserving source content and keeping human judgment where automated checks are not enough.

Use this playbook for Docs Distiller and adapt the same loop to other agentic workflows. The implementation status below is specific to DD Ingestion; Planner, Synth and other workflows still need their own frozen-input replay adapters and domain-specific graders.

## Refinement loop

1. **Freeze the evidence.** Keep an immutable input snapshot and identify the exact source/run/page or task. Record content hashes. Never compare results from different source snapshots as if they were the same test.
2. **Attribute each result.** Record Git SHA, build fingerprint, algorithm/normalizer version, prompt hashes, model/provider label, inference settings, source identity, input hashes, and run ID. Do not store API keys or credential-bearing endpoint URLs.
3. **Turn confirmed failures into fixtures.** Each fixture has a stable ID, source snapshot reference, labels, a specific expected behavior, and a human rubric for any judgment that cannot be reduced to deterministic checks. Include false positives and successful cases as well as failures.
4. **Replay a small case first.** Re-run the exact failing page/task through the changed components without re-fetching inputs or publishing production artifacts. Then run the affected regression set, followed by the shared cross-domain set when shared code changed.
5. **Use layered graders.** Make deterministic checks authoritative for exact transformations, schema validity, source preservation, idempotence, coverage, ordering and safety constraints. Use the agent's task-specific rubric for semantic judgments, calibrated against reviewed examples. Ask a human to disposition ambiguous findings. A single LLM judge is not a release gate.
6. **Separate failure types.** Track source/extraction defects, deterministic transform defects, agent quality, invalid structured output, provider errors, latency, cost and human review separately. Retry bounded transient provider failures; report a failed or unavailable model call as inconclusive, never as a content-quality pass.
7. **Promote only after gates pass.** First run the targeted fixture, then the affected tier/agent regression set, then one fresh full end-to-end representative. Use a full multi-source pass after shared changes. Keep each experiment tied to one deployed build.
8. **Converge with evidence.** A source/build pair is ready when end-to-end delivery succeeds, all required content is accounted for, deterministic goldens and safety checks pass, no high-impact quality issue remains unresolved, failures are either recovered or explicitly inconclusive, and human review has dispositioned ambiguous cases. Re-run only stochastic cases when a retry could affect the decision; do not repeat a long full run merely to obtain another model sample.

## What each layer should prove

| Layer | Fast-loop evidence | End-to-end confirmation |
|---|---|---|
| Ingestion | Frozen pages, normalizer idempotence, structure and link preservation, audit schema, evidence fidelity, repair safety | Tier discovery, extraction coverage, preparation, audit/repair, publish, vault/backfill |
| Planner | Frozen page metadata and plan inputs, exact coverage/partition invariants, stable chapter design, exclusion rationale | A fresh plan on a representative corpus and successful chapter selection in the UI |
| Synth | One frozen chapter/source set, source coverage, citations, code fidelity, learning-objective coverage, output structure | One chapter end-to-end first; then a full synthesis after single-chapter quality converges |
| Other agentic workflows | Persisted task inputs, deterministic invariants, structured output and safety checks, human-reviewed semantic cases | One production-like run through all side effects and completion checks |

For workflows that modify data, replay into an isolated artifact namespace and omit publish/finalize side effects. Keep the original source immutable. If an offline replay cannot faithfully reproduce an upstream preparation stage, state the replay boundary in its report and retain one live end-to-end check for that stage.

When adding a replay adapter for Planner, Synth or another agent, keep a common report envelope: evaluation/run ID, source snapshot IDs and hashes, build/prompt/model provenance, replay boundary, per-stage status and timings, usage/error counters, deterministic checks, semantic-review labels, artifact references and an explicit `publishes`/side-effect flag. The domain-specific fixture and grader should live beside that workflow's implementation; the shared playbook should describe the acceptance loop, not impose one agent's quality rubric on another.

## DD Ingestion implementation

- Full tier benchmarks remain the end-to-end checks for tiers 1–4. Tier 5 (GitHub) is excluded from the current refinement program.
- Fixed-input replay is available for reviewed pages from the 2026-11-13 r2 representative runs. It reuses the saved agentic page input, applies the current deterministic normalizer, auditor and repairer, writes only beneath a hidden evaluation namespace, and never extracts, publishes or backfills vaults.
- Replay reports include source/build provenance, prompt and pipeline-code hashes, model configuration, source page hashes, stage progress, deterministic checks, bounded structured audit findings and patch decisions, LLM counters, artifact keys and human-review rubrics. They do not persist raw model responses. Full-tier benchmark reports also hash the exact captured source-file inventory and prepared Agentic inputs.
- Curated fixtures live in [`apps/server/domains/dd/ingestion/evaluation/fixtures.json`](../apps/server/domains/dd/ingestion/evaluation/fixtures.json). Add a fixture after reviewing a real failure; pin it to an immutable benchmark run and page index, and include at least one objective assertion where possible.
- Durable reports live under `dd-evaluations/{evaluation_round_id}/{replay_id}.json`. A completed execution can still have a failed deterministic gate or pending human review; execution success and quality acceptance are separate fields.

Read the active server's safe build fingerprint and agent configuration before starting a round:

```http
GET /api/v1/dd/debug/provenance
```

Start all curated fixtures for one source run:

```http
POST /api/v1/dd/debug/replays/{source_round_id}/{source_run_id}
Content-Type: application/json

{"evaluation_round_id":"dd-replay-r1"}
```

Select a subset by page index:

```json
{"evaluation_round_id":"dd-replay-r1","page_indices":[30,33,65]}
```

Read the result:

```http
GET /api/v1/dd/debug/replays/{evaluation_round_id}/{replay_id}
```

Read a page for source/input/output comparison (`version` defaults to `output`):

```http
GET /api/v1/dd/debug/replays/{evaluation_round_id}/{replay_id}/pages/{page_idx}?version=source
```

The endpoint only accepts completed benchmark runs that have matching curated fixtures. It shares the DD single-flight gate and returns `locked` while another ingestion or replay is active; retry after that run is terminal. A replay is a development evaluation aid, not a replacement for live source extraction checks.

Cancel a running replay using the existing run cancellation endpoint with the returned `replay_id`:

```http
POST /api/v1/dd/ingestion/runs/{replay_id}/cancel
```

## Current DD sequence

The deployed v25 evaluator/fixture follow-up completed a second targeted replay across Tiers 1–4. Its fingerprint was `def5e86ba86fc10ea6387b75762c2be3203a51bfce22902aecbcaf5a1c68342f`. All four deterministic gates passed for the curated cases from source round `dd-refine-2026-11-13-r2`: Optimum `[0, 5, 6, 10]`, Asyncio `[0, 13, 69]`, DeepAgents `[30, 33, 65]`, and Unsloth `[46, 169]`. The API single-flight gate means jobs run sequentially. Replay IDs and per-tier findings are in [`DD-INGESTION-CONVERGENCE-PLAN.md`](DD-INGESTION-CONVERGENCE-PLAN.md).

1. Preserve the Optimum page-0 `library_name` repetition because it appears in the official reference. Page 10's earlier `graph_module` finding did not recur; retain that as a non-reproducible sampled finding unless a reviewed source comparison confirms a defect.
2. Preserve Unsloth page 46's bare image markers and keep the low-severity source-quality finding; do not invent image targets. Page 169's deterministic heading fix and forbidden-evidence check passed.
3. The local follow-up now injects a replay-only auditor provider failure for DeepAgents page 65, requires `audit_status: unavailable`, and checks that normalized source bytes are preserved. Its offline check passed. After manual redeploy, confirm a new provenance fingerprint and replay only page 65; the expected unavailable fixture should be listed separately from unexpected outages and the deterministic gate should pass.
4. After that replay passes, run one sequential fresh full Tier 1–4 acceptance round on the deployed build. Check source extraction, page counts and exclusions, publication, vault/backfill, audit/repair availability, and reviewed source/output examples. Page-only replay gates are not tier convergence.
5. Run the separate Claude Code Tier 1 acceptance check after validating the large-changelog splitter against the saved raw input; this is separate from the four curated replay sets.

The current evidence establishes that all four tiers passed their selected fixed-input regression gates, not that the tiers have converged. Record any new code revision and replay round separately; do not compare results as if they shared a build or source snapshot. Do not call a tier converged based solely on lower finding counts; check page retention, fidelity, unavailable audits/repairs and reviewed source/output samples too.

## Full-tier checkpoint — 2026-11-14

The fresh full-tier baseline `dd-refine-2026-11-14-r1` completed on deployed fingerprint `ccdd8f9d9d9eb80971dc41cf92ef854f5c997b0a045eb57b0f4cabe9e71fbde7`. All four representatives reached publish and vault backfill. Tier 2 reported no findings or review pages; Tier 1, Tier 3 and Tier 4 still had review-required or unavailable audits. Full results are recorded in [`DD-INGESTION-TIER-TESTS.md`](DD-INGESTION-TIER-TESTS.md).

The next reported redeploy did not change the server provenance: the live auditor hash remained `dc710fb3a3bffe121420ae3a4da0943287fa058725a63ee533544165df3a0b8a`, while the current workspace hash is `4741105f7659675c13900ebb6acda236be05962158ce058881adc67202fa2f8f`; both auditor and repair prompt hashes also differ. Stop at provenance preflight in this situation instead of spending another full-tier run on the old build. Run the local regression scripts, ask for another manual redeploy, verify the expected hashes at `GET /api/v1/dd/debug/provenance`, and then start the next sequential full round. Do not run Tier 5.

### Update after full-tier round `dd-refine-2026-11-14-r3`

The deployed build matched the workspace provenance and completed the full Tier 1–4 pass, including publish and backfill for every tier. No tier converged; the per-tier metrics and source-preservation notes are in [`DD-INGESTION-TIER-TESTS.md`](DD-INGESTION-TIER-TESTS.md). This round confirms end-to-end operation on the deployed build, while leaving quality review and provider reliability open.

The next local changes address evidence quality across Sphinx and ordinary Markdown: line wrapping inside Sphinx definition bodies, valid italicized default-parameter signatures, repeated explanations across separate API entries, escaped emphasized identifiers absent from cited evidence, and complete inline links misreported as missing-bracket syntax. The DeepAgents page-80 fixture and five Asyncio page fixtures are pinned to the r3 full-tier source runs. The checks do not alter page text; regression tests keep same-entry repetition and genuinely broken links reviewable. They pass locally, but the code and fixtures need manual redeployment before fixed-input replay. Replay DeepAgents page 80 and Asyncio pages 0, 4, 42, 54 and 99; compare exact source/output hashes, then choose whether another full-tier run is needed. Keep Tier 5 excluded.

The user redeployed fingerprint `9379ea0eed7d478dfacbf0cb18a2a420110eab022a0789e975bda06ec5be6cc3`. The targeted non-publishing replays both passed their deterministic gates: DeepAgents page 80 had zero findings, and Asyncio pages 0, 4, 42, 54 and 99 had zero findings. There were no provider errors, unavailable audits/repairs or accepted patches; source/input/output hashes matched on all six pages. Replay IDs and fixture details are in [`DD-INGESTION-TIER-TESTS.md`](DD-INGESTION-TIER-TESTS.md). The fixtures' evaluator state still requests human disposition because each includes a review rubric; the manual review is recorded in that log. These results validate the selected cases only, so retain prior full-tier reliability findings and target those remaining issues before deciding on another full round.

### Structured-output recovery and source-safe convergence

Treat provider unavailability, schema-invalid output, valid-but-suspicious source content and actual Markdown defects as separate outcomes. A missing confidence score can use a conservative zero default so it does not invalidate an otherwise complete audit; a malformed response should get one schema-specific correction attempt. Keep repair eligibility gated on high audit/patch confidence, exact evidence, exact source hash and the existing patch validator. If repair still fails, retain the original page and mark the stage unavailable.

When filtering repeated or table-related claims, require source structure as evidence: scoped occurrences under distinct installation-method headings are separate workflows; a valid GFM row may contain empty values or `N/A`; and inconsistent link styling alone is not broken link syntax. Keep real editorial typos, uncertain external identifiers and ambiguous repeated cards visible for human/source-owner disposition. Do not patch wording or guess at external facts.

The DD refinement follow-up adds replay fixtures for the r3 source pages that triggered these cases and extends the evaluator to check expected repair status and forbidden finding descriptions. A targeted replay of every previously flagged page is sufficient to decide whether Tier 3 or Tier 4 has converged when the full run already confirms extraction/publication coverage and the targeted run preserves exact source/output hashes. A full-tier rerun is still needed when a change affects extraction, page partitioning, normalizer transformations or full-coverage accounting. Confirm the updated provenance before spending model calls; run replays sequentially under a single-flight gate.
