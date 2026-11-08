# Docs Distiller agent capability SOTA review — 2026-11-08

## Decision

Keep the active Ingestion, Planner v2, and Synth v2 prompts and deterministic in-process tools as the baseline. The 2026-11-08 live assessment did not find an Ingestion content-cleanup defect; the Planner and Synth fixes are local and still need the corrected Planner rerun and a Synth run for plan chapter 01. Adding tools or rewriting prompts before those runs would make the next comparison harder to interpret.

The highest-value capability changes are, in order:

1. Validate the already-applied fixes on the same DeepAgents corpus: deploy, rerun Planner v2, then synthesize chapter 01 only.
2. Add an adjudicated offline evaluation set and use it for prompt/skill ablations. Change a prompt or skill only when it fixes a scored failure without harming the other measures.
3. Upgrade DeepAgents in an isolated dependency change and test pinned skills. This removes a repeated skill-discovery/read step for roles whose skill is required on every call.
4. Keep MCP tools opt-in by role. Use an official-docs MCP server only for version checks during Synth, and a separately isolated execution server only for code verification. Ingestion and Planner do not need MCP for the work they do.

This follows the live assessment's control boundary: deterministic requirements stay in graph nodes; agents handle bounded judgment, synthesis, and repair.

## What the last run says

- **Ingestion:** 99 pages were finalized. No Mintlify footer links, code-fence attributes, or annotations remained. The one documentation-index match was inside a source example and was correctly retained. There were provider errors and unavailable page repairs, but no observed corpus-cleanup defect. Do not add a web or docs MCP lookup to the auditor: its evidence must remain the frozen page and deterministic scan.
- **Planner v2:** coverage was 99/99 with no unplaced pages, but the design needed deterministic reconciliation, produced 25 chapters against a roughly 12-chapter design, and misclassified instructional chapters as appendices. The local prompt, retry, and classification fixes directly target those failures. A new live plan must establish whether they worked.
- **Synth v2:** the only completed artifact was plan-order 21, not chapter 01. Its render passed the audit, but the study-order, render-pointer, and title defects were only fixed locally. Chapter 01 has no live quality result yet.
- **Comparison:** neither v2 has yet been shown to outperform v1 in an agreed comparison. Keep v1 as a rollback path until the current fixes and a rubric comparison pass.

Source record: [DD DeepAgents pipeline live assessment](DD-DEEPAGENTS-PIPELINE-LIVE-ASSESSMENT-2026-11-08.md).

## Prompt and skill decisions by stage

| Stage / agent | Keep as the core contract | Improvement to ship after the next live sample |
|---|---|---|
| Ingestion page auditor | One page; Markdown-only evidence; exact excerpts; conservative defect categories; valid-export conventions; structured `PageAssessment`; no repair authority | Build a small labeled audit set from the DeepAgents false positives and real defects. Score precision and recall per category. Add examples to the prompt only for repeatable errors the set exposes. |
| Ingestion page repairer | Exact-page/version scope; minimal patch; exact target and context; `check_patch` before answer; same deterministic gate after answer | Evaluate accepted patches against human-labeled diffs. Keep the tool as the only custom tool; do not add generic editing, browsing, or filesystem tools. |
| Planner v2 page-card agent | One page per grounded card; keep-biased treatment of reference pages; short fields; no invented content | Score `keep`, kind, topic, and best-code coverage separately. Add a role skill only if repeated card errors are not already addressed by the shared schema and prompt. |
| Planner v2 chapter designer | Select a measured candidate partition; assign each supplied group once; distinguish capstone fit from tutorial/reference classification; preserve all kept pages | Report invalid-answer and reconciliation rates, chapter count, chapter objective coherence, page/code coverage, and reading-time spread. Add few-shot examples only from reviewed plan decisions; do not ask the model to repair deterministic invariants. |
| Synth outliner and chapter writers | Source-grounded sections and code; canonical ledger hashes; explicit prerequisite order; exact citations; check tools; bounded retries | Score complete must-teach coverage, unsupported claims, duplicate examples, structural validity, and judge outcomes. Make the prompt/skill pair a single tested versioned unit. |
| Synth capstone designer and verifier | Compose taught examples; `check_code` for deterministic checks; explicit verified/unverified/failed result | Keep execution outside the worker process. Treat parse/import checks as static verification only; never describe them as proof that code runs. |
| Synth reviewer and judge | Read-only section inspection; targeted revision; binary criteria with specific evidence; deterministic checklist gates remain authoritative | Calibrate on the same labeled chapters. Track false pass and false fail separately; a judge outage should remain an infrastructure status, not a content failure. |

The current prompt files already contain the main high-value techniques: role-specific output contracts, untrusted-input boundaries, evidence requirements, explicit procedure, bounded tool loops, and deterministic checks. A wholesale prompt rewrite has no evidence-based case yet. Deep Agents describes skills as progressive context and supports tools supplied directly to each role; that fits the current least-privilege design ([overview](https://docs.langchain.com/oss/python/deepagents/overview), [skills](https://docs.langchain.com/oss/python/deepagents/skills), [tools](https://docs.langchain.com/oss/python/deepagents/tools)).

### Skill wiring fix found in this review

Planner v2 already passed `design-chapter-groups/SKILL.md` as a virtual file, but its `chapter_designer` agent did not register that skill, allow reading the skill path, or allow the `read_file` tool; the prompt also did not tell it to read the skill. That made the skill unavailable in practice. The local fix now registers the role skill, grants read access only under that skill directory, allows only one `read_file` call, and names the skill in the system prompt. This is the one immediate prompt/skill change warranted by the code inspection. It must be included in the corrected Planner live rerun.

## Skills

The repo currently has ten role skills, but the Synth architecture notes that only the outliner, reviewer, and drift detector use skills on repeated runs. Keep skills specific to a task that benefits from reusable procedure; do not add a skill that repeats its system prompt or duplicates deterministic code.

The current lock has `deepagents==0.6.8`. Upstream's latest stable release at this review date is `0.7.34` (released 2026-11-05); pinning a skill in run input requires `deepagents>=0.7.23` ([release history](https://github.com/langchain-ai/deepagents/releases), [pin-skills documentation](https://docs.langchain.com/oss/python/deepagents/skills#pin-skills)). After the live baseline:

1. Upgrade DeepAgents in a dedicated branch, resolve the full dependency lock, and smoke-test all agent constructors, state schemas, middleware/profile behavior, structured outputs, and existing skill file loading.
2. Pin only always-required skills: Planner's chapter designer and Synth's outliner, reviewer, and capstone designer where those roles already receive skills. Do not pin a skill that is not used on that call.
3. Compare pinned vs. discovered skills on identical scripted and representative LLM cases. Record answer quality, tool/model calls, input tokens, and latency. Keep pinning only if it removes an unnecessary model turn without reducing quality.
4. Consider per-framework `code-standards` skills after a chapter-01 review demonstrates that generated examples violate the framework's local idioms. Derive each from the ingested corpus or a user-reviewed gold chapter; do not assume one style guide fits every framework.

This is a staged migration, not an automatic dependency bump: the project uses DeepAgents 0.6.8 and several roles configure harness profiles, so the newer library must be checked against these call paths before it becomes the production floor.

## In-process tools and MCP choices

Use the smallest boundary that fits the job. Deep Agents accepts local functions and LangChain tools as well as MCP tools; the protocol adds value when the tool crosses a process, service, or trust boundary, not merely because the function is useful ([Deep Agents tools](https://docs.langchain.com/oss/python/deepagents/tools)).

| Option | Maturity/version as of 2026-11-08 | Fit for DD | Decision |
|---|---|---|---|
| Existing deterministic in-process tools | Project code; no protocol or external release lifecycle | `check_patch`, `validate_outline`, ledger queries, `check_section`, `check_code`, and structural planner checks | **Default.** Fast, local, testable, and already scoped to the role. |
| LangChain official docs/reference MCP | Hosted endpoints: `https://docs.langchain.com/mcp` and `https://reference.langchain.com/mcp`; service content is rolling, not a package release | Synth code derivation for LangChain-family docs when corpus version/API drift needs a targeted check | **Optional, Synth only.** Read-only; preserve the ingested corpus as the book's source of truth. Store returned URLs and version evidence. Never let a live lookup silently rewrite a chapter claim. |
| Context7 MCP | Hosted rolling service at `https://mcp.context7.com/mcp`; installation docs do not identify a pinned server release | Fallback docs lookup across frameworks without an official MCP endpoint | **Optional later.** Use only when the docs/version are identified and the result can be attributed. Prefer the framework's official docs/reference endpoint when available. |
| Isolated code-execution MCP server | No DD sandbox service currently exists; project `SubprocessExecutor` is intentionally disabled because it does not isolate the worker host or network | Synth verification of a capstone or runnable example | **Required before execution is enabled.** Self-host behind a separate sandbox boundary; no network egress, no host mounts/secrets, prebuilt versioned images, and strict CPU, memory, file, process, and wall-time limits. Return exit status and bounded stdout/stderr as evidence. |

Do **not** connect a general-purpose docs/search MCP to Ingestion or Planner. Ingestion should assess the page it froze. Planner should use its cards, code ledger, candidate scores, and deterministic validators. For Synth, official docs lookup is a narrowly scoped drift check, while the user's chosen corpus version remains authoritative.

### MCP dependency and protocol status

The server lock currently has `langchain-mcp-adapters==0.3.0`, `fastmcp==3.4.11`, and `mcp==1.30.0`; no DD agent currently calls an MCP server, and the app's own MCP endpoint exposes only `ping`. The `langchain-mcp-adapters` repository is archived. LangChain's replacement `langchain.mcp.MCPAdapter` is documented as beta and currently requires `langchain[mcp]>=1.4.0`, so it is not a safe drop-in on the existing locked stack ([LangChain MCP docs](https://docs.langchain.com/oss/python/langchain/mcp), [adapter repository](https://github.com/langchain-ai/langchain-mcp-adapters)).

MCP's `2026-07-28` specification is current. The official Python SDK's stable v2 line is at `mcp==2.3.0` (released 2026-10-02); SDK v1 is in maintenance mode. The server still locks v1. Before exposing a new remote tool, choose and validate a compatible supported client/server stack; do not assume the current lock implements the new protocol ([MCP Python SDK releases](https://github.com/modelcontextprotocol/python-sdk/releases), [2026-07-28 specification notes](https://blog.modelcontextprotocol.io/posts/2026-07-28/)).

The MCP specification release adds stateless request handling, per-request protocol metadata, cache hints, and authorization hardening. Those are useful operational features for a real service boundary, but they do not improve local deterministic functions by themselves. Any remote server still needs a minimal tool allowlist, bounded inputs/results/timeouts, explicit authorization, and end-to-end tracing.

## Evaluation plan: the highest-return agent improvement

Use offline regression evaluations before live changes and monitor outcomes after deployment. The evaluation guidance distinguishes offline benchmarks/regressions from online monitoring; use code checks for exact structure and an LLM or human rubric for semantic quality ([LangSmith evaluation types](https://docs.langchain.com/langsmith/evaluation-types)). The evaluator can remain a repo script; LangSmith is not required.

Create a small versioned dataset from the finalized DeepAgents corpus and reviewed output:

- **Ingestion:** valid export conventions (quoted tabs/callouts, aligned tables, code examples), true duplicates, real malformed headings, damaged text, repeated furniture, and outage cases. Label findings, evidence spans, and safe patch decisions.
- **Planner:** all 99 source keys; reference vs. instructional pages; must-teach code IDs; approved chapter objectives and prerequisite edges; invalid/partial designer outputs to exercise reconciliation.
- **Synth:** once chapter 01 runs, label must-teach code coverage, citations against source pages, code duplication, tutorial step order, capstone composition, verification status, and review decision. Add the hand-built LangGraph chapters as pairwise references where they cover the same material.

For each prompt/skill/tool change, compare against the same records and record:

- **Ingestion:** finding precision/recall by category, safe auto-patch precision, unchanged-page rate, unavailable-page rate, calls/tokens per page.
- **Planner:** source and best-code coverage, duplicated/unplaced pages, chapter count/coherence, capstone classification, reading-time spread, fallback/reconciliation rate.
- **Synth:** must-teach coverage, citation support, duplicate code, deterministic gate pass, judge pass, verified/unverified/failed split, rewrite count, calls/tokens/latency.

Promote a change only if its target quality metric improves and no hard gate, source coverage, or rollback measure regresses. Treat judge disagreement and provider outages separately from content quality.

## Ship order

1. Deploy the existing local fixes and verify the DD Pages quality panel with a new run only if fresh cleanup counts are required.
2. Rerun Planner v2 on the same 99-page corpus; review its grouping and reconciliation metrics.
3. Run Synth v2 for chapter 01 only; stop before chapter 02 and record the chapter rubric and execution status.
4. Freeze the reviewed examples as an offline evaluation set. Run prompt and skill ablations on each affected role.
5. Upgrade DeepAgents in isolation and measure pinned skills against the same set before raising the production dependency floor.
6. Add official-docs MCP to Synth only if chapter review exposes version/API drift that the ingested corpus cannot answer. Keep the endpoint and source URLs in the output evidence.
7. Build a separate no-egress sandbox service before adding an execution MCP tool to Synth. Enable execution only after its boundary and resource limits are demonstrated.
8. Compare v2 with the retained v1 outputs on the same corpus and rubric. Remove rollback implementations only after the v2 quality and reliability gates pass.

## Evidence and freshness

- Deep Agents describes tools, skills, filesystem context, and subagents as harness capabilities: [overview](https://docs.langchain.com/oss/python/deepagents/overview), [skills](https://docs.langchain.com/oss/python/deepagents/skills), [tools](https://docs.langchain.com/oss/python/deepagents/tools). Pages accessed 2026-11-08.
- Latest DeepAgents release observed: `0.7.34`, released 2026-11-05; project lock: `0.6.8`: [official release history](https://github.com/langchain-ai/deepagents/releases).
- Pinning skills requires `deepagents>=0.7.23`: [official skill docs](https://docs.langchain.com/oss/python/deepagents/skills#pin-skills). Accessed 2026-11-08.
- LangChain MCP supports a docs MCP endpoint and a separate API reference endpoint; the LangChain adapter API is marked beta: [programmatic docs access](https://docs.langchain.com/use-these-docs), [MCP integration](https://docs.langchain.com/oss/python/langchain/mcp). Accessed 2026-11-08.
- Context7 installation documents its hosted MCP URL and setup: [installation](https://context7.com/docs/installation). Accessed 2026-11-08.
- MCP `2026-07-28` spec and Python SDK lifecycle/releases: [spec release notes](https://blog.modelcontextprotocol.io/posts/2026-07-28/), [Python SDK releases](https://github.com/modelcontextprotocol/python-sdk/releases). Accessed 2026-11-08.
- Offline regression and online monitoring evaluation patterns: [LangSmith evaluation types](https://docs.langchain.com/langsmith/evaluation-types). Accessed 2026-11-08.

Claims about the best stage-to-tool mapping and rollout sequence are recommendations inferred from these primary sources plus DD's saved run data and current code; they are not claims that any unrun upgrade or MCP integration has already improved a live result.
