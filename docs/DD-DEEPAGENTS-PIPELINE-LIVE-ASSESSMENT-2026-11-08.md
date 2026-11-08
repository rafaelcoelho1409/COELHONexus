# DD DeepAgents pipeline: live assessment and fixes (2026-11-08)

Scope: the finalized DeepAgents ingestion corpus, the latest Planner v2 plan, and the first dev Synth v2 study. Synth content review is limited to the one chapter that actually rendered; the requested plan chapter 01 has not run yet.

## DeepAgents fit

The official [Python Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview) describes the harness as intended for complex, multi-step work, with context management, tools and filesystem backends, subagent isolation, skills, and steering. That matches Planner and Synth: both run long, staged workflows over large corpora, and the current designs use bounded agents, role skills, read-only context backends, validation tools, and deterministic graph nodes around model decisions. Keep required deterministic steps in LangGraph nodes and reserve agents for judgment, synthesis, and repair. Ingestion follows that boundary: normalize deterministically, use page-level agents for ambiguous audit/repair, and gate edits with deterministic checks. There is no evidence for adding another layer of agents.

## Ingestion

The finalized candidate is `ad8d071c50af44c9b82ed9ef3130afd2`, with 99 processed pages. Read-only inspection of all processed pages found:

| Residue check | Pages matching |
|---|---:|
| Mintlify footer links (“Connect these docs”, “Edit this page”) | 0 |
| Code-fence attributes | 0 |
| Code annotations | 0 |
| Documentation Index banner pattern | 1 |

The one banner match is page 45, *Retrieval Augmented Generation (RAG) with Deep Agents*. It appears inside a documentation example showing scraped output, so retaining it is correct. The current normalization and repair safeguards are working on this corpus; this run did not identify a content-cleanup defect. The additive control-character cleanup below improves parity with BD's quality report rather than addressing residue found in this corpus. The candidate run completed with 99 pages; its logs show three repairs unavailable after provider timeouts and six provider errors, handled without losing the source pages. The earlier 2026-11-07 report remains the forensic record of the preceding run and its offline fixes.

The current local catalog includes `/oss/python/releases/` while excluding only the changelog, but this finalized manifest contains no release URLs. This run therefore does not verify release-note coverage. Confirm whether the official sitemap currently has DeepAgents release pages before spending another ingestion run.

### Pages quality report

DD's Pages reader now has the same expandable quality summary and issue detail display as BD. It reads the immutable assessment and patch report for the page from the current finalized Agentic AI run; accepted patches appear as fixed automatically, rejected patches include their suggested replacement, and auditor or repair outages appear in run notes. The report also carries deterministic cleanup counts from the corpus normalizer. Non-whitespace Unicode control characters are now removed and counted, and the report presents them as a medium-severity `Control characters` finding with the automatic-removal count.

This is a source change only. The endpoint, OpenAPI contract, and generated web client are updated locally; the UI has not been exercised and the change has not been deployed. Existing immutable reports do not gain cleanup counts retroactively. A new Agentic AI run is needed to produce the expanded report for existing pages.

## Planner

The current v2 plan covers all 99 sources and reports 100% page coverage with no unplaced pages. Its chapter architect returned an invalid structured answer once (`ChapterDesignAnswer`), so deterministic reconciliation repaired 17 missing groups. The plan grew to 25 chapters although the design statistics suggested about 12; its first eight chapters are labeled appendices despite substantial instructional code (chapter 01, *Deep Agents Architecture and Context*, has 54 best-code items). The code incorrectly treated “these examples do not form one capstone application” as “this content is reference-only,” and the designer call used one attempt despite the configured retry count of two.

Fixes applied locally:

- Keep tutorial chapters as tutorials when `capstone_ability` is false; appendix classification now follows whether the source is reference material.
- Do not reject a complete design solely because instructional examples do not compose into one application.
- Use the configured two-attempt policy for the chapter designer.
- Clarify in the architect prompt that capstone fit and tutorial-vs-reference classification are separate decisions.
- Compare a diverse range of counts without a fixed chapter maximum; the eight-partition limit controls prompt size only.
- Let a chapter be excluded from Synth only with a reason. Deterministic reconciliation now keeps any excluded chapter that would remove a unique must-teach code family or source-card teaching point from the eligible tutorial path.

The deployed plan is still the pre-fix plan. A Planner rerun is needed to produce the corrected chapter layout before a representative Synth chapter can validate that layout.

## Synth

The canceled dev study requested 25 chapters and completed one. Its first artifact was `ch-0b45da8491d6`, plan order 21, *Text Splitter Integrations and Strategies (Reference)*. It passed the render audit, cited its source, resolved both references, and used 2 of 4 available code blocks. It took about 216 seconds with 11 LLM calls. The next chapter was canceled after three calls. The study did not run plan chapter 01, *Deep Agents Architecture and Context*.

Three defects explain the run and the UI symptoms:

1. The study endpoint sorted chapter IDs lexicographically instead of preserving the Planner’s chapter array. This made plan order 21 run first.
2. Synth v2 writes `render-v2-latest.json`; the study sidebar, resume-skip check, book harmonizer, and pipeline-state probe read only `render-latest.json`. A valid v2 render therefore appeared unrendered after refresh, leaving completion status at zero.
3. The web client replaced plan-hydrated titles with ID-derived placeholders when the study started and when it received `study_start`.

Fixes applied locally:

- Preserve Planner order when queuing study chapters.
- Make render-pointer readers version-aware; keep the pipeline-state probe compatible with both v1 and v2 pointers.
- Retain hydrated chapter titles and completed status when resetting run cells from IDs.

The current dev deployment still has the old behavior. No new study was started, and no later chapters were synthesized. After deployment and a corrected Planner run, assess Synth with the explicit single-chapter endpoint for plan chapter 01 rather than starting a 25-chapter study.

## Status

The source fixes, shared Ingestion/Planner/Synth usage breakdown, and DD Pages quality report have not been deployed. Synth chapter 01 remains unassessed live: the completed artifact was plan order 21, and the local chapter-order fix has not been deployed. Planner v2 and Synth v2 are defaults, but are not yet demonstrated to be better than v1. Keep both v1 implementations available until a corrected Planner run, a chapter-01 Synth run, and an agreed v1/v2 comparison are complete. Both v1 branches remain reachable through `DD_PLANNER_VERSION=v1` and `DD_SYNTH_VERSION=v1`.

## Local follow-up (2026-11-08)

The chapter-design implementation now scores the full available hierarchy and sends up to eight diverse partitions to the architect, with no fixed chapter-count cap. Page basenames and Synth eligibility/reasons are exposed in Planner health and the Synth chapter list. Synth study dispatch skips ineligible chapters while preserving all chapters and their `.md` source names for reference. The exclusion guard protects both unique best-code families and unique source-card teaching points. Synth already builds its per-chapter ledger from every assigned source page and its checklist blocks a draft that misses required best code; these checks establish the coverage mechanism, not the quality of an unseen chapter-01 output.

Planner and Synth LLM usage sheets and graph-node drawers now use the same accessible breakdown as Ingestion, including the same totals, grouping control, provider/model columns, error/loading/empty states and keyboard-scrollable table. Graph nodes open on Usage, with Overview, Activity and Raw I/O still available.

At the time of this follow-up, `test_dd_chapter_design.py` passed all six checks; Python compilation passed for the changed Planner modules and scripts; the web typecheck passed; the focused web suite passed 88 tests across 8 files; and Biome passed on the touched UI paths. The Ingestion agentic-input script now asserts its `(normalized Markdown, cleanup counts)` return contract, but its run timed out during the full AI package import before producing assertions. The full Planner/Synth graph regressions were stopped after remaining silent for several minutes. No live Planner rerun or Synth chapter-01 run had yet been started.

Deep Agents fit remains sound for these workflows: the harness is designed for complex multi-step work with planning, filesystem context, delegation and long-term memory; required deterministic work remains in graph nodes while model judgment stays in bounded agents ([official Python overview](https://docs.langchain.com/oss/python/deepagents/overview)). Existing architecture notes: [Planner](DD-PLANNER-AGENTIC-AI-2026-11-06.md), [Synth](DD-SYNTH-AGENTIC-AI-2026-11-06.md), and [the prior ingestion report](DD-AGENTIC-DEEPAGENTS-ASSESSMENT-2026-11-07.md).

## Follow-up after the reported 80-chapter plan (2026-11-08)

The user reports that a subsequent Planner run recommended 80 chapters and did not start Synth. The run's chapter-design stats, selected partition, final plan artifact, and deployed source revision were not captured here, so the exact stage that produced 80 and whether the count was justified cannot yet be independently audited. An 80-chapter plan is a strong fragmentation signal for this corpus and conflicts with the product goal unless the source cards establish that many non-combinable learning objectives.

Local Planner changes now subtract a soft logarithmic cost for counts above the suggested chapter count and a separate severity-based cost for chapters substantially over the reading target. Candidate prompts no longer include the one-page-per-chapter extreme as a default anchor. The designer is asked to repair a plan when its tutorial count is more than twice the soft estimate, while being told that this is not a hard maximum. Remaining design review flags are retained in the chapter-design stats and proposal artifact. Source coverage and the best-code/concept exclusion protections remain in place.

The Pipeline UI now reloads the final chapter plan when Planner ends, before optional Auto-chain starts Synth. A successful Planner Wipe clears the Synth chapter-list and selected chapter state in the same view; Wipe is disabled while another pipeline stage is active. These changes are local and have not been deployed or UI-verified.

Ingestion assessment remains based on the earlier 99-page live run: the then-live agentic stage missed export furniture and made three unneeded or damaging edits. Deterministic normalization and safer audit/repair gates were implemented locally, and the Pages quality report shows cleanup and review decisions for new runs. No raw result from the latest ingestion was supplied, so its post-fix quality and current audit coverage still require a fresh run review. For the next Planner result, capture the chapter-design candidate/selected metrics and final chapter list, then check whether the two-times-estimate repair flag remains; do not start Synth until the plan has been reviewed.
