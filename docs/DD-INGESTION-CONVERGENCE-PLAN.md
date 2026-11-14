# DD Ingestion convergence plan

**Updated:** 2026-11-14  
**Scope:** Docs Distiller ingestion tiers 1–4. Tier 5 (GitHub) is out of scope.

## Recommendation

Keep full tier benchmarks as end-to-end release checks, but stop using them as the inner loop for every code or prompt change. The r4 live pass took about 51 minutes across the four representative sources and surfaced useful defects, but repeating all of that work for every fix spends time on stable extraction and makes code changes harder to separate from upstream source and provider variation.

Use fixed-input replay evaluations for development, then run one fresh full ingestion per affected tier after the replay gates pass. A full all-tier run remains appropriate after shared normalizer/auditor changes. The deployed v25 evaluator/fixture follow-up passed its fixed-input replay gates across all four tiers. A local replay-only outage-injection follow-up now has an offline check; after deployment, validate it with the targeted DeepAgents page-65 replay before starting the fresh full all-tier acceptance pass.

This is consistent with current agent-evaluation practice: use curated regression cases, combine deterministic, model-based and human graders, and repeat trials selectively where model output varies. See [Anthropic's agent evaluation guide](https://www.anthropic.com/research/demystifying-evals-for-ai-agents), [LangChain's evaluation types](https://docs.langchain.com/langsmith/evaluation-types), and [OpenAI's evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

## Implementation steps

1. **Make every result attributable.** Record the Git SHA, normalizer version, prompt revision/hash, model/provider and inference settings, tier/source URL, source content hashes, and run ID. Keep each round tied to one deployed build and one frozen input snapshot. Do not compare results across different source snapshots as if they were the same test.

2. **Use the non-publishing replay runner.** DD captures immutable raw Markdown inputs before later processing in [`storage/service.py`](../apps/server/domains/dd/ingestion/storage/service.py). The development replay API selects curated pages from a saved benchmark and runs the current normalizer, auditor, and repairer without re-fetching or publishing. It saves outputs and evaluator results under a separate evaluation artifact prefix. Keep live tier runs for crawler/extractor validation.

3. **Create labeled, tier-specific regression sets from actual failures.** Keep the source page, expected deterministic output, issue labels, expected audit disposition, and repair-safety expectation together. Start with the r4 cases below and add every newly confirmed regression. Sample by page type and failure mode; pages split from the same source are correlated and should not be treated as fully independent evidence.

   | Tier | Initial cases from r4 | Main behavior to protect |
   |---|---|---|
   | 1 — `llms_full` | Unsloth pages 169, 46, 38, 55, 71, 203 and 52 | Emoji heading repair; figure image syntax; contextual repeats across updates, variants and tutorials; structured audit output |
   | 2 — `llms_txt` | Optimum pages 2, 4 and 10 | List recovery; duplicate scope within versus across API entries; exact Returns/Parameters export repair |
   | 3 — `sitemap` | DeepAgents pages 30 and 65 | Escaped table continuation; valid code-fence evidence; structured audit output |
   | 4 — `docs` | Asyncio pages 0, 30, 40, 13, 33 and 69 | Sphinx structure; valid escaped signatures and blockquotes; structured audit output; prior false-positive regressions |

4. **Separate functional quality from provider reliability.** Track extraction/coverage, deterministic normalization, audit correctness, repair correctness, structured-output validity, provider failures, latency and token use independently. Retry bounded transient transport/rate-limit failures; classify malformed or semantically invalid output separately. Retain bounded failure diagnostics and provider metadata without persisting raw model bodies by default. A provider error must not be counted as a content-quality pass.

5. **Use mixed graders.** Make deterministic checks the gate for exact transformations, schema validity, link integrity, page ordering, idempotence, and preservation of source content. Treat the agent's semantic findings as candidate judgments, calibrate them against reviewed fixtures, and use human review to label ambiguous findings and distinguish ingestion defects from upstream editorial issues. A separate LLM judge is optional and must be calibrated; never use one LLM judge as the sole authority.

6. **Gate fixes with replay before live ingestion.** Run the targeted case that motivated the change, then the affected tier's full replay set, then the shared regression set. Compare the candidate to a fixed baseline on identical inputs. Repeat only stochastic or ambiguous cases when the result could change the decision; do not repeat a 200-page source run just to see if a single model response changes.

7. **Run live tier benchmarks after replay passes.** Shared normalizer and auditor changes still warrant one full live pass across tiers 1–4. Confirm the deployed fingerprint and normalizer version. Run the shorter representatives first—Tier 2 Optimum, Tier 4 Asyncio, Tier 3 DeepAgents, then Tier 1 Unsloth—and stop early if a broad regression appears. A tier-specific extraction change requires a full live run for that tier; a shared downstream fix can be validated with replay plus live passes for tiers in scope.

## Convergence gates

A tier is converged for a **specific source snapshot and build** when all these conditions hold:

- The full run reaches successful extraction, Agentic processing, publish, and vault backfill.
- Extracted and retained pages are accounted for. Every drop or exclusion has an explicit, correct reason; no required source content silently disappears.
- Deterministic golden cases pass exactly and normalization is idempotent.
- The labeled audit/repair regression set has no unresolved high-impact false positives, missed defects, or unsafe patches. Any source-quality finding left unchanged has an explicit disposition.
- No page remains review-required because of an unexamined pipeline or structured-output failure. Transient provider failures are recovered or recorded as an inconclusive case for targeted retry.
- There are no regressions in other tiers touched by shared code, and latency/provider reliability remain visible as separate operational measures.

Do not require two full live rounds merely to demonstrate repeatability. A clean full run plus passing replay gates is the normal acceptance bar; repeat targeted trials for nondeterministic cases. Revalidate when upstream documentation or the model/provider configuration changes materially.

## Current checkpoint

The full Tier 1–4 baseline `dd-refine-2026-11-14-r1` completed on deployed v25 (`ccdd8f9d9d9eb80971dc41cf92ef854f5c997b0a045eb57b0f4cabe9e71fbde7`). Tier 2 had zero reported findings or review pages; Tier 1 had 15 review-required pages, Tier 3 had two, and Tier 4 had three unavailable audits/review pages. No tier is converged. After the user reported a redeploy, the live provenance endpoint still returned the r1 auditor/prompt hashes, while the workspace calculates changed hashes and fingerprint `16121696f0bb64630ded9769491990f336348e6171842990b4cf5e289d361b2c`. The next full round is therefore held at preflight until the service at `localhost:23030` reports the workspace hashes. Tier 5 remains excluded.

## Claude Code ingestion incident

The manual Tier 1 `claude-code` run `27f3649f7a0f4d68bbf47a2fbb9a5322` extracted 356 pages, then failed before completing any audits. Its page 2 was a 1.25 MB changelog whose releases are introduced by standalone bold version lines such as `**2.1.323**`; the splitter recognized `<Update>` tags and Markdown headings but not this release format, so the fragmented-changelog safeguard retained the oversized page. The UI accurately showed `Failed` and `0 audited`.

The local fix splits this changelog into self-contained groups capped around 120,000 characters, preserving whole release entries and tagging generated pages as lookup-only changelog content. As a safety net, any remaining page above the auditor limit is retained unchanged, marked audit-unavailable for review, and skipped by repair instead of aborting the full ingestion. Validate this against the saved Claude Code raw input after deployment, then confirm the full 356-page run reaches normalization and publication. No tier convergence claim is made until that fresh run completes and its review items are assessed.

A local source-conservation replay of the bold-version splitter against the saved raw changelog reconstructed all 1,255,111 input characters exactly after removing only the synthetic headings. It found 440 version boundaries and produced 12 pages; the largest release group was 118,966 characters. This validates the splitter's partitioning behavior. It does not validate the complete pipeline's relative-link normalization, audit, repair, or publication stages; those still require the fresh deployed run.

## Refinement-loop tooling shipped locally — 2026-11-13

The repository now has a fixed-input page replay path to shorten the inner refinement loop:

- `GET /api/v1/dd/debug/provenance` returns the current Git SHA, component-code and prompt hashes, model label/settings, normalizer versions and deterministic build fingerprint; use it to confirm the manually deployed build before a replay or live round.
- `POST /api/v1/dd/debug/replays/{source_round_id}/{source_run_id}` queues the curated fixtures for a completed tier benchmark. Pass `{"evaluation_round_id":"dd-replay-r1"}` to run all fixture pages for that source, or add `page_indices` to select a subset.
- `GET /api/v1/dd/debug/replays/{evaluation_round_id}/{replay_id}` reads the durable report. `GET /api/v1/dd/debug/replays/{evaluation_round_id}` lists a round.
- `GET /api/v1/dd/debug/replays/{evaluation_round_id}/{replay_id}/pages/{page_idx}?version=source|input|output` reads source and replay Markdown for human comparison.
- The replay reads immutable, already-prepared Agentic AI page inputs from the source benchmark, re-applies the current corpus normalizer, auditor and repairer, and writes artifacts only to a hidden evaluation namespace. It does not fetch pages, alter the source benchmark, publish a manifest, or backfill vaults.
- Replay and live benchmark tasks use the DD single-flight gate. They are sequential with normal ingestion and with each other. Existing `POST /api/v1/dd/ingestion/runs/{replay_id}/cancel` can request cancellation; the replay worker persists a cancelled report and releases the gate.
- Reports record Git SHA, service and normalizer versions, prompt and pipeline-code SHA-256 values, model label/configuration, a deterministic build fingerprint, immutable source-page hashes, replay-stage progress, deterministic gate results, agentic findings/repairs, LLM counters and human review rubrics. Full-tier reports also hash the captured source-file inventory and prepared Agentic inputs. API credentials and full provider URLs are excluded.
- The fixture set in `apps/server/domains/dd/ingestion/evaluation/fixtures.json` covers verified false positives, deterministic export repairs, a table continuation, an exact-duplicate safety case, and a source-preservation scenario for audit outages. The deployed runner used in R2 did not inject provider failures and the sampled DeepAgents call succeeded. A local follow-up now injects that failure only inside replay and asserts the unavailable status plus byte preservation; outage preservation remains unverified until redeployment and replay. The evaluator checks normalizer idempotence, fixture-specific stats, prohibited evidence, repair budgets, and byte preservation when audit/repair is unavailable. Semantic findings remain marked for human disposition.
- The normalizer's existing `escaped_table_row_pipes_fixed` and `optimum_fx_return_sections_repaired` counts are now retained in per-page mechanical-fix records, so replay assertions can see those transformations.
- Full-tier benchmark reports also get prompt/config provenance and an aggregate hash of the frozen Agentic AI page inputs.

This tooling and the generic workflow are documented in [`AGENTIC-AI-REFINEMENT-PLAYBOOK.md`](AGENTIC-AI-REFINEMENT-PLAYBOOK.md). The replay runner has now completed one round against live MinIO/LLM services; the results and remaining limits are recorded below. Tier 5 remains excluded.

## Targeted replay round `dd-replay-2026-11-13-r1`

After redeployment, the API reported corpus normalizer v22, agentic normalizer v3 and build fingerprint `22c7b7431c65d32a00c5703220cd791155f127f65c65b5ccc7222ba44728699e`; no live ingestion was active. All source benchmarks came from immutable r2 reports. Every replay report records `publishes: false`.

| Tier | Replay | Result | Finding |
|---|---|---|---|
| 1 — Unsloth | `0c3d75d3c3f6402a8c31fd3b68a41953` | Deterministic gate passed; 2 pages, no unavailable calls, 0 accepted repairs | Emoji heading fix triggered once. The figure-marker evidence assertion passed, though one unrelated `other` finding remains for human review. |
| 2 — Optimum | `3bcd456c938a4960846c24deb270e736` | Evaluator gate failed on 2 of 4 pages; no unavailable calls, 0 repairs | Pages 0 and 6 were incorrectly expected to increment the aggregate API-export counter. Page 10 correctly triggered the v22 Returns/Parameters repair. Page 0 exposed an additional `create_register` export defect. |
| 3 — DeepAgents | `ed6896bda3ae4e1ea75ada63ab806f7a` | Deterministic gate passed; 3 pages, no unavailable calls, 0 repairs | Table rejoin fixed 4 escaped continuation rows. One provider call returned 504; the audit recovered. The replay took 125.7s, mostly due to this call. |
| 4 — Asyncio | `c765df92867b4d1fb989f3f397dcf6d6` | Deterministic gate passed; 3 pages, no findings, no unavailable calls, 0 repairs | Sphinx signatures, heading spacing and definition-list samples remained unchanged. |

The r2 page-0 Optimum input has `config.0` immediately followed by the `Callable[[str, Tuple[str, ...]], Callable[[Type], Type]]` return type and its decorator description. The [official Optimum reference](https://huggingface.co/docs/optimum/exporters/task_manager#createregister) separates this into a `Returns` section. The first v23 rule did not match the frozen input: the actual source line is indented and begins `Whether or not the register function...`, while the matcher expected an unindented `Whether the register function...`. The targeted replay caught this before acceptance.

After the user's v23 redeploy, replay `cc5375b5ac1b40199f2a9a3dbc1800e3` completed in 6.1 seconds against pages 0, 6 and 10 with `publishes: false`. The deterministic gate failed only page 0's expected normalizer counter. Page 6 remained unchanged with zero accepted repairs; page 10 triggered the existing Returns/Parameters repair once. All three audits completed without provider errors, with zero accepted/rejected patches. The v24 local matcher now uses the exact observed phrase and allows its export-added indentation, while remaining scoped to the `TasksManager.create_register` section and exact concatenation. The v23 failure is retained as evidence that fixture expectations alone are insufficient: replay the actual saved input after deployment.

Replay reports include bounded structured audit findings and patch-decision summaries so reviewers can inspect cited evidence without a second storage lookup. After the user's v23 redeploy, replay `cc5375b5ac1b40199f2a9a3dbc1800e3` completed in 6.1 seconds against Optimum pages 0, 6 and 10 with `publishes: false`. The deterministic gate failed only page 0's expected normalizer counter because the v23 matcher expected different wording; page 6 stayed unchanged and page 10's existing boundary repair fired.

## All-tier targeted replay round `dd-replay-2026-11-13-r3` — 2026-11-13

After redeployment, provenance reported corpus normalizer v24, agentic normalizer v3 and fingerprint `dea1b83f99b2c0a4b07be05c63877edb6b81fc646200bf3c08e58413477ced33`; no live ingestion was active. Four isolated replay jobs covered 12 curated pages across Tiers 1–4. The API single-flight gate required the jobs to run sequentially. Every report has `publishes: false`.

| Tier | Replay | Pages | Result |
|---|---|---:|---|
| 2 — Optimum | `1a3bf2aa40c046e8bf9d7dc21158aa20` | 4 | Deterministic gate passed. Two medium audit findings remained; one provider 504 was recorded, with no unavailable pages. Both v24 Optimum return-boundary repairs fired on pages 0 and 10. Page 6 still has an inline `.0` return fragment duplicated in its later Returns block. |
| 4 — Asyncio | `7c1bb833d3884026b3ddd488eb7fa76c` | 3 | Gate passed, zero findings, zero provider errors or repairs. |
| 3 — DeepAgents | `03d8459f128f44728ed30d67afdc106a` | 3 | Gate passed, zero findings/errors. Four escaped table continuation rows were restored on page 30. |
| 1 — Unsloth | `21325e4ef2874aff9c9037932dc53285` | 2 | Gate passed, one low-severity audit finding remained on page 169. Its evidence was `Q\&A` while its description claimed an escaped underscore; this is an evidence/description mismatch, not a real defect. The emoji-heading fix fired once. |

Optimum's page-0 `library_name` repetition also appears in the [official Tasks Manager reference](https://huggingface.co/docs/optimum/exporters/task_manager#optimum.exporters.tasks.TasksManager.get_model_from_task). It is upstream source duplication, so the normalizer should preserve it rather than silently delete source text. The page-6 `.0` concatenation is a separate export artifact; v25 adds a content-preserving rule that removes the inline fragment only when its complete text is present in a later `Returns` block in the same heading scope. The page-169 finding motivates a general auditor evidence check: explicit escaped-character claims must match the cited literal character. The fixture now forbids the known `Q\&A` evidence.

No tier converged from this page-only round. Every fixture still has a human-review rubric, and these replays do not verify fresh extraction, complete page coverage, publish or vault backfill. Local v25 normalizer/auditor changes are not deployed. After manual redeploy, confirm provenance and rerun the four curated replay sets; if their deterministic and human dispositions are clean, proceed to one sequential full Tier 1–4 acceptance round.

## All-tier targeted replay round `dd-replay-2026-11-14-r1` — 2026-11-14

The user redeployed the v25 build. Provenance reported corpus normalizer v25, agentic normalizer v3 and fingerprint `d7fa4ffed8619e7a4461f098d39ff05a2150148c07c023628dfee8efb3e30f56`; the active-run endpoint was empty before starting. Four isolated replay jobs covered the same 12 curated pages and all report `publishes: false`. The API single-flight gate required sequential execution.

| Tier | Replay | Pages | Result |
|---|---|---:|---|
| 2 — Optimum | `d29e29d05d754cc79c2038ae2ce34c82` | 4 | Deterministic gate passed; no unavailable audits/repairs or provider errors. The generic page-6 repair fired once; aggregate `api_return_export_duplicates_removed` was 2 (pages 6 and 10). Two medium findings remain: repeated `library_name` text on page 0 and a claimed missing `graph_module` parameter on page 10. No repairs were accepted; two proposals were rejected. Preserve the page-0 repetition because it is also present in the official reference. Review the frozen page-10 source before deciding whether its finding is real; the current [official Optimum reference](https://huggingface.co/docs/optimum/en/torch_fx/package_reference/optimization) lists `graph_module` under `__call__`. |
| 4 — Asyncio | `200898ef1f574bab9d4a0f352d380620` | 3 | Gate passed; zero findings, provider errors, unavailable stages or repairs. |
| 3 — DeepAgents | `f56ad51b7840401bb7375c5dc3b4cc70` | 3 | Gate passed; zero findings/errors and no unavailable stages. Four escaped table continuation rows were restored. The structured-output outage case completed successfully, so the unavailable-stage preservation branch was not exercised in this run. |
| 1 — Unsloth | `d3f49788f7d84c1a907e3738af65ac95` | 2 | One low-severity `markdown_formatting` finding on page 46 is supported by the frozen input, which contains bare `!` markers without image targets. The page-169 emoji-heading fix fired and the escaped-character false positive did not recur. The deterministic gate failed because the old fixture incorrectly prohibited exact evidence `!`; this was a fixture mismatch, not a repair failure. |

The Unsloth page-46 marker is a source-fidelity issue: the captured `llms-full` page is missing image targets, while the [current official page](https://unsloth.ai/docs/new/studio/sandboxing-in-unsloth) contains image sources for those positions. Do not invent URLs or remove the markers. The fixture now expects the auditor to report `markdown_formatting` with zero accepted repairs. The evaluation domain now supports `expected_issue_categories`, and `forbidden_evidence` checks snippets within cited evidence rather than exact equality. These evaluator/fixture follow-ups are local and need deployment before the next replay round.

No tier converged from this page-only round. Asyncio and DeepAgents have clean sampled results; Optimum still has one unresolved medium finding and Unsloth has a source-quality finding requiring explicit disposition. All fixtures carry human-review rubrics, and this round does not validate full extraction coverage, publication or vault backfill. After deploying the evaluator/fixture follow-up, rerun the four replay sets. If deterministic checks and human dispositions pass, run one sequential full Tier 1–4 acceptance round.

## All-tier targeted replay round `dd-replay-2026-11-14-r2` — 2026-11-14

After the evaluator and fixture follow-up was deployed, provenance reported corpus normalizer v25, agentic normalizer v3, evaluator code hash `3d45dcd6212e774937b9c8c6072d7e6bb16e20c71c8f4e2bfc1a5688c7719d98`, and fingerprint `def5e86ba86fc10ea6387b75762c2be3203a51bfce22902aecbcaf5a1c68342f`. The active-run endpoint was empty before the round and after all jobs completed. All four reports have `publishes: false`; jobs ran sequentially through the DD single-flight gate.

| Tier | Replay | Pages | Result |
|---|---|---:|---|
| 2 — Optimum | `38fe6536f0c0428cb1aaa40ebaf96995` | 4 | Deterministic gate passed; no provider errors, unavailable audits/repairs, accepted patches or rejected patches. One medium `duplicate_boilerplate` finding remains: repeated `library_name` text on page 0, which is also present in the official reference and should be preserved. Page 10's prior `graph_module` claim did not recur. The page-6 export normalization assertion passed. |
| 4 — Asyncio | `7f49f25ca14f465e99807f7d7e13390b` | 3 | Gate passed with zero findings, provider errors, unavailable stages, repairs or mechanical changes. The Sphinx signature, heading and definition-list samples stayed unchanged. |
| 3 — DeepAgents | `3c9008fbd71944afb226234e038ea47d` | 3 | Gate passed with zero findings/errors, unavailable stages or repairs. Four escaped table continuation rows were normalized. The page labeled as a structured-audit outage received a valid audit response, so this replay did **not** exercise the outage-preservation branch. |
| 1 — Unsloth | `bbf8fbadf58e446896fbe052381103a7` | 2 | Gate passed with 2 successful audits, no provider errors/unavailable stages and zero accepted/rejected patches. Page 46 produced the expected low-severity `markdown_formatting` finding for bare `!` image markers; the source was preserved. Page 169's emoji-heading fix fired once, and the invalid `Q\&A` evidence did not recur. |

This round resolves the prior deterministic fixture mismatch: Unsloth page 46 now requires the expected issue category rather than forbidding the exact `!` evidence, and the page-169 check correctly treats `Q\&A` as a forbidden evidence substring. The generic duplicate-return normalization and all other deterministic assertions passed. No production ingestion regression was exposed by these 12 pages; the round did expose an outage-test coverage gap, addressed in the local evaluator follow-up.

The Optimum page-0 repetition is confirmed upstream content and should remain unchanged. The Unsloth page-46 marker is present in the frozen export while the current official page has image targets; preserve the captured source and retain the low-severity source-quality finding rather than inventing targets. The deployed R2 report did not exercise DeepAgents outage handling. A local follow-up now injects a replay-only auditor provider failure for page 65 and requires `audit_status: unavailable` plus byte preservation; the offline check in [`test_dd_ingestion_evaluation.py`](../scripts/server/test_dd_ingestion_evaluation.py) passes. This follow-up still needs redeployment and a targeted page-65 replay before outage handling can be considered verified.

No tier is converged yet. These non-publishing page replays validate the deployed downstream pipeline on sampled frozen inputs, but do not prove fresh extraction, complete page accounting, publish, vault backfill, or full-tier quality. The fixture rubrics also remain pending human disposition in the reports. Next: manually redeploy the local evaluator follow-up, confirm its new provenance, rerun DeepAgents page 65, then run one fresh full Tier 1–4 acceptance round if the failure-preservation gate passes. Keep Tier 5 excluded.

## Full-tier refinement status — 2026-11-14 r3

The redeployed build completed a sequential full Tier 1–4 run; all four reached publication and vault backfill. Details and findings are recorded in [`DD-INGESTION-TIER-TESTS.md`](DD-INGESTION-TIER-TESTS.md). No tier converged. Tier 1 still has one repair-unavailable review page and 31 provider errors; Tier 2 has one source-preserving repeated-parameter finding; Tier 3 has 13 findings and 15 provider errors, including three broken-link claims on page 80 that need exact evidence inspection; Tier 4 has six Sphinx-heavy findings, two unavailable audits and two review pages.

Local sanitizer work filters five grounded Sphinx/evidence cases: ordinary wrapped prose, valid italicized default-parameter syntax, repeated text whose exact occurrences belong to distinct Sphinx API entries, an escaped emphasized identifier absent from the cited source excerpt, and “missing bracket” claims contradicted by complete inline Markdown links. Same-entry repetition and genuinely broken links remain reviewable. DeepAgents page 80's three reported examples were checked against exact source and all links are complete; a fixture now pins this false-positive case to the r3 source run. Five more fixtures pin the Tier 4 cases (pages 0, 4, 42, 54 and 99) to the same full-tier source run. Both offline checks pass. These changes and fixtures were made after r3 and need manual redeployment before validation. Next run non-publishing fixed-input replays for DeepAgents page 80 and Asyncio pages 0, 4, 42, 54 and 99; verify source/output hashes and issue dispositions, then decide whether another full Tier 1–4 round is justified. Do not run Tier 5.

The user redeployed matching fingerprint `9379ea0eed7d478dfacbf0cb18a2a420110eab022a0789e975bda06ec5be6cc3`; the targeted replay round `dd-replay-2026-11-14-r3` then passed for DeepAgents page 80 (replay `7011297ebb01475caf638940951badcc`) and Asyncio pages 0, 4, 42, 54 and 99 (replay `d27910ca659a4d4c8393cf5e939acb1c`). Both deterministic gates passed, with zero findings, provider errors, unavailable audits/repairs or accepted repairs. Each tested page retained an identical source/input/output hash. The precise results and human dispositions are recorded in [`DD-INGESTION-TIER-TESTS.md`](DD-INGESTION-TIER-TESTS.md). The evaluator still labels the rubric fixtures as requiring human review; do not confuse this with failed checks. These targeted passes validate the specific auditor fixes only; they do not erase Tier 1/2/3/4 signals from the preceding full run or establish tier-wide convergence. Proceed with targeted investigations of the remaining issues and avoid another costly full run until those investigations justify it.

## Next checkpoint — structured-output and table false-positive follow-up

The saved r3 page records show Tier 1 page 22 had an unavailable optional repair, Tier 4 pages 49 and 96 had unusable auditor outputs, and Tier 3 still had speculative findings about valid table values/link styling. Local changes now apply schema-feedback retries to malformed auditor/repair responses, default omitted audit confidence to zero, filter only evidence-backed table presentation claims, and recognize repeats scoped to separate package-installation headings. The source remains protected by the same exact-evidence patch gate.

The replay evaluator now checks expected repair status and forbidden finding descriptions; fixtures pin all pages with prior Tier 3 findings, the two Tier 4 audit failures, Tier 1 pages 22/197, and Tier 2 page 0. Component provenance now includes the shared DD schemas/service and patch-gate files that were previously omitted from the fingerprint.

No new live replay ran: `localhost:23030` stopped accepting connections during read-only page inspection. After the user manually redeploys, verify provenance first, then replay Optimum `[0]`, Asyncio `[0, 4, 42, 49, 54, 96, 99]`, DeepAgents `[27, 41, 70, 71, 74, 80, 90, 104, 105]`, and Unsloth `[22, 197]` sequentially. The fixed-page replays cover all previous Tier 3/4 finding pages, so those tiers can reach a convergence decision without another full run if the gates pass, source/output hashes match, and human dispositions are accepted. Tier 1 still has a broader 47-page finding distribution; assess it separately before calling it converged. Tier 5 remains excluded.
