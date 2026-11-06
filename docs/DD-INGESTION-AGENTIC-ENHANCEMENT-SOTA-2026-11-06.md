# DD Ingestion Agentic AI — enhancement study and decisions (2026-11-06)

Inputs: the deepagents chapters in `~/Workbench/AIAgentsStudies` (001–010, deepagents 0.7.32), the current ingestion code (`ai/dd/{agents,nodes,graphs,prompts,skills}/ingestion`, deepagents 0.6.8 installed), the two Evidently runs and the Locust runs already assessed, and a sota-search (sources at the end).

## What the studies teach that DD does not use yet

| Chapter | Pattern | DD today |
|---|---|---|
| 008 skills | One skill source per role; a custom subagent never inherits skills; `scripts/` inside a skill hold the deterministic checks; skills judged by a with/without-skills baseline (`compare`, `matrix`) | Two roles, one skill each, never measured against "no skill" |
| 008 critic | Mechanical checks run first, the LLM critic second; a fresh context; an ensemble of differently-roled prompts | Mechanical checks run after the model, as a gate |
| 008 `WriteGuardMiddleware`, 009 policy probe | Authority is enforced by code and proven by a scripted-model probe, not by the prompt | `permissions` deny writes; no probe |
| 010 middleware | Retry, fallback, call limits, tool-error sanitising, `RubricMiddleware` (beta, grader with structured output) | Retry, limits and tool-error are in `agents.service.resilience`; no fallback/rubric |
| 008 chat | A context profile (64k) so summarisation triggers inside the smallest arm's window | Not checked for DD's `build_chat_model` |
| 008/009 `served_by` | Every run is described by which models answered | Already in the LLM usage snapshot |

## Findings in DD's own code

1. **Bug: tool exclusions leak between agents built in one process.** `agents.service.build` calls `register_harness_profile("openai:fgts-va", …)` with the role's `excluded_tools`. Registrations are additive and excluded-tool sets **union** (deepagents 0.6.8, `register_harness_profile` docstring), and every role uses the same Rotator model name, so the key is shared. Reproduced offline: build a role that allows only `read_file`, then one that allows `grep/glob/ls/write_file`: the registry now excludes `edit_file, execute, glob, grep, ls, task, write_file, write_todos` for both. A Celery fork-pool process lives across tasks, so a planner or synth role built after an ingestion run can silently lose `grep`, `glob`, `write_file` or `task` (the architect's critic subagent). Not seen live yet because planner and synth have not run live.
2. **The skill read is a model round trip that is always taken.** Both ingestion roles need their one skill on every call, so progressive disclosure saves nothing and costs up to one extra call per page on a pool where some models skip it. The skill and `SYSTEM.md` also repeat the same never-report rules, and the export-convention list exists a third time in `page_audit/patterns.py`.
3. **The audit's output is almost unused.** Nothing in planner or synth reads `page_kind` or `summary`; what changes a page is the title (≥0.85) and accepted patches. On both Evidently runs no page body changed, for 110 audit calls and about 470k input tokens. A clean Tier 1 bundle should come out unchanged; the agentic stage earns its cost on Tier 3 and 4 pages, which carry real navigation and footer furniture.
4. **The auditor invents repeats.** In the second Evidently run all 6 surviving duplicate findings quoted text that occurs once. The code now drops them, but the model still spends tokens on a claim a one-line search refutes.
5. **A rejected patch is lost.** 20 of the 23 rejections in the first Evidently run were "target does not occur exactly once", which the model could have fixed in the same turn if told.

## Decisions

### Team shape: keep the fixed graph, do not add an orchestrator agent

Per-page work is independent and its path is known (audit, then repair, then publish). Anthropic's own guidance is the simplest pattern that works: workflows (chaining with code gates) for predictable steps, agents only where the path cannot be fixed; context isolation, the reason to use subagents, is moot when each agent sees one page. Keep `audit_pages → normalize_pages → publish`. An evaluator LLM is justified only when criteria are fuzzy; ours are mechanical, so the evaluator is code.

### Per deterministic piece

| Where | Code today | Decision | Why |
|---|---|---|---|
| audit | Page size cap, frozen-hash check, immutable write, resume cache | **Keep** | Idempotence and replay; a model cannot give these |
| audit | `structured_or_recovered` (fenced JSON from the answer) | **Keep** | A property of the model pool, not of the task |
| audit | Evidence must be an exact substring ≤2,000 chars | **Keep** | The one check that makes every finding verifiable |
| audit | Export-convention filter (`patterns.py`) | **Keep, make the prompt derive from it** | One list in code, rendered into the prompt, so they cannot drift |
| audit | Duplicate needs ≥2 occurrences, same-evidence merge | **Keep, and move into the schema** | Add `other_evidence` (the second copy, exact substring, different position), required for duplicate categories: the model must point at the repeat before it can claim one |
| audit | Finding discovery of syntax defects (`##Install`, mojibake, repeated paragraphs, empty headings) | **Replace the LLM here with deterministic detectors** that hand the auditor a candidate list | Evidence is real by construction; the model judges "defect or intended" instead of searching. Cascade of cheap detectors first, judge second |
| audit | Page kind, summary, title proposal | **Keep with the LLM** (semantic); make summary/kind optional until a consumer exists | Not derivable by rules |
| repair | Eligibility (patchable category, confidence ≥0.90) | **Keep** | Policy |
| repair | `apply_patches`: sha, exact-once, context, overlap, fence guard (any indent), word guard, duplicate-survivor | **Keep as the final gate, and also expose it as a tool** (`check_patch`) | The gate stays code; the same function returns the specific rejection to the repairer inside its loop, so it can retry |
| repair | Title: confidence ≥0.85, substring of the page, marker stripped | **Keep** | Substring check is the anti-hallucination guard |
| normalize | `strip_site_suffix`, `disambiguate_titles` across pages | **Keep** | Needs all pages at once and must be stable across runs; no agent sees them all |
| normalize | Replay of saved patches for resume | **Keep** | Determinism |
| agents | Resilience middleware, unreviewed-tool guard | **Keep, fix the leak (below)** | |

Rule used: **authority, idempotence and anything that must be exact stays code; semantic judgement stays with the model; a check the model could use to correct itself becomes a tool, with the same function as the final gate.**

### Tools and MCP

In-process LangChain tools, not MCP servers. The two tools worth having are `scan_page` (the deterministic detectors, so the auditor can re-scan a section) and `check_patch` (dry-run of one patch through `apply_patches`, returning an actionable error such as "target occurs 3 times; extend it with the adjacent text"). MCP adds a process and a transport for code that already lives next to the agent. The one MCP server found, `markdownlint-mcp`, fixes about 58% of markdownlint's rules, has no adoption and could not be verified at its repository; do not adopt it. Revisit MCP only to expose DD's validators to outside clients.

### Prompt and skill structure

- **Prompt (one source of truth per concern):** role in one line, then the untrusted-data boundary, then the decision procedure, then the output contract, then calibration. Stable text first and the page last, so the long data does not bury the instructions. Keep it short and concrete for a mixed pool: a free model follows a table of never-report patterns better than a paragraph about them.
- **Skills only where knowledge is conditional.** Replace "one skill per role, always read" with a skill per *page shape*, picked by code from the tier and the bundle (`mdx-export-conventions`, `html-converted-boilerplate`, `sphinx-autodoc-artifacts`) and injected, not discovered. On deepagents ≥0.7.23 that is `pinned_skills`; on 0.6.8 it is a prompt section. Keep each under the spec's limits (name ≤64 chars matching the directory, description ≤1,024 in third person with trigger words, body under 500 lines, one level of references).
- **Degrees of freedom:** the repair skill is a narrow bridge (exact steps, exact output); the audit's title/summary part is an open field.
- **Poka-yoke over prose:** a constraint that can live in the schema (`other_evidence`, absolute `source_sha256`) goes there, not in the prompt.

### Bug fix design (P0)

Stop using `register_harness_profile` for per-role exclusion. Give each role its own small allowlist middleware (filters `request.tools` in `wrap_model_call`) and leave the shared registry untouched. Keep `GeneralPurposeSubagentProfile(enabled=False)` out of the registry too by not defining subagents for single-agent roles (the general-purpose subagent only costs a graph build). Add the reproduction as a regression test: build a strict role then a wide one in one process and assert the wide one still sees its tools.

## Plan, in order

| # | Change | Expected effect | Needs |
|---|---|---|---|
| P0 | Allowlist middleware instead of shared harness profiles; regression test | Planner/synth roles keep their tools after an ingestion run | Your go-ahead (`agents/service.py` is your file) |
| P1 | Fold the skill into the prompt for the two roles; generate the never-report list from `patterns.py` | Up to one model call less per page; one source of truth | |
| P2 | Deterministic detectors → candidate list in the payload; `other_evidence` for duplicates | No invented repeats; fewer tokens; auditor judges | |
| P3 | `check_patch` tool in the repairer | Fewer lost patches (20 of 23 rejections were fixable) | |
| P4 | Evaluation set from saved runs (Locust 24 + Evidently 70 pages with their known outcomes) plus 20 Tier 3/4 pages; compare agentic vs no-agentic on accepted patches, false findings per page, tokens per page, wall time | Shows whether the stage earns its cost where defects are real | Ingest one Tier 3 and one Tier 4 framework first |
| P5 | Optional: shape-specific skills, pinned (needs deepagents ≥0.7.23 in the server venv) | | Dependency bump |

## Shipped (2026-11-06, offline-tested, not deployed)

| # | Change | Where |
|---|---|---|
| P0 | `ToolAllowlistMiddleware` per role; `isolate_harness_profile` clears the shared model key and registers a neutral profile (no base prompt, no general-purpose subagent, no exclusions) right before each agent is built; subagents get their own allowlist (`allowed_tools`, read tools by default) | `middleware/service.py`, `agents/service.py`, both ingestion `agent.py`; `scripts/server/test_dd_agent_tools.py` |
| P1 | Skill folded into each role's `SYSTEM.md`; `skills/ingestion/` and the `files` plumbing through graph, nodes and domain wiring removed; the never-report list is rendered from `EXPORT_CONVENTIONS` (the table the audit filter's pattern is tested against); `page_audit/patterns.py` moved to `agents/ingestion/page_auditor/patterns.py` | `prompts/ingestion/*/SYSTEM.md`, `agents/ingestion/page_auditor/{patterns,domain}.py` |
| P2 | Page scans (`detectors.py`: heading without space, empty heading, damaged characters, repeated prose block outside fences) put `candidates` in the auditor request; `PageIssue.other_evidence`; `is_repeat` replaces the "occurs twice" rule: a repeat holds when the evidence occurs twice or the named second copy is the same text in another place | `nodes/ingestion/page_audit/{detectors,domain,node}.py`, `agents/schemas.py` |
| P3 | `check_patch` tool for the repairer, reading the page and audit from the agent's state; `patch_rejection` is the one function behind both the tool and `apply_patches`; refusal hints per reason | `tools/ingestion/check_patch/`, `nodes/ingestion/page_repair/{domain,params,node}.py` |
| P4 (tooling) | `scripts/dd/dump_agentic_run.py` (in a server pod) and `scripts/dd/replay_agentic_run.py` (local): replay a saved run through today's scans, sanitiser and gate with no model | `scripts/dd/` |

Replay of the second Evidently run: 3 leads (all real repeats), 6 of 9 stored findings dropped by today's rules (the 6 invented repeats), 3 kept; request 7.3k chars per page on average, auditor prompt 6.2k chars. The first run's assessments were already wiped; only its inputs remain.

Behaviour changes to watch on the first live run: the auditor has no tool (up to one call fewer per page, no skill read); the repairer now may make several `check_patch` calls (bounded at 10, model calls at 8); a duplicate finding with neither `other_evidence` nor a literal second occurrence is dropped. Not done: P4's live comparison on Tier 3/4 pages and P5 (deepagents bump), which wait on that comparison. `test_ingestion_tier1.py` and `test_ingestion_tier2.py` fail with `'FakeStore' object has no attribute 'run_id'` in files this work does not touch.

## Comparison of designs

| Approach | Maturity | Trade-offs | Pick when |
|---|---|---|---|
| **Fixed graph + detectors + judge agents + validator tools** (recommended) | Anthropic's chaining-with-gates and tool-feedback patterns; aider/Anthropic edit tools use the same exact-match-and-explain loop | Detectors are code to maintain; small and testable | Always, for per-page normalisation |
| Current: fixed graph + single-shot agents + code gates | Working, measured on 94 pages | Spends calls on discovery and on a skill read; loses rejected patches | Fallback if P2 and P3 do not pay off |
| Orchestrator deep agent with auditor/repairer/critic subagents | Documented, flexible | Unpredictable path for a predictable job; more calls on flaky free models; subagent context isolation is irrelevant for one page | A task whose steps cannot be fixed in advance (planner architect, not this) |
| LLM judge replaces code gates | Common in evals | Not reproducible across model rollouts; cannot give idempotence; one preprint shows auditors fabricating findings as batches grow | Subjective checks only, after the cheap checks |
| MCP servers for linting | Early, thin | Extra process, partial fixes, unverified | Sharing validators with external clients |

## Evidence

- Workflows vs agents, prompt chaining with programmatic gates, evaluator-optimizer, "simplest solution possible": Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents), 2024-12-19.
- Tool design (actionable error messages, poka-yoke, token-efficient responses, fewer higher-level tools, eval-driven iteration): Anthropic, [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents), 2025-09-11.
- Skill spec (name/description limits, 500 lines, scripts/references/assets, one level deep, validate loops, plan-validate-execute, degrees of freedom, evaluation-driven authoring): [agentskills.io/specification](https://agentskills.io/specification) and Anthropic's [skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), both undated.
- Deep Agents skills (three disclosure levels, custom subagents do not inherit skills, `pinned_skills` ≥0.7.23, `include_tools` ≥0.7.22) and subagents (`response_format` ≥0.5.3, isolated vs fork, "only give subagents the tools they need"): [skills](https://docs.langchain.com/oss/python/deepagents/skills), [subagents](https://docs.langchain.com/oss/python/deepagents/subagents), undated; the fetch returned part of each page as a summary.
- Additive harness profiles, union of excluded tools: deepagents 0.6.8 source (`register_harness_profile` docstring) and the offline reproduction in this session.
- Exact-match edits with errors fed back to the model: [Anthropic text editor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool) (versions to `text_editor_20250728`; the error strings come from a summary of the truncated page) and aider's [unified diffs article](https://aider.chat/2023/12/21/unified-diffs.html), 2023-12-21 (old models; flexible patch application cut editing errors ninefold).
- Cheap deterministic stage first, judge second: [LegalWiz](https://arxiv.org/pdf/2510.03418) (NLI filter plus LLM, fewer false positives), a different domain, so adjacent evidence only.
- LLM auditors fabricate findings and degrade with batch size, so verify every finding against the source mechanically: arXiv 2609.09696, seen only as a search result, a preprint, not opened.
- `markdownlint-mcp`: aggregator listings only, repository not checked.

## Caveats

- Several fetched pages came back partly summarised, and neither the skills spec nor the best-practices page is dated; limits quoted are from the spec text but the version of Deep Agents each feature needs is from docs, not release notes.
- Python Deep Agents numbering is inconsistent across sources (a changelog entry reads 0.6.0 for May 2026 while the studies run 0.7.32); the venv here has 0.6.8, so `pinned_skills` and `include_tools` need a bump first.
- The claim that nothing downstream reads `page_kind` or `summary` comes from a code search of planner, synth and the DD domain; the web ingestion view reads the assessment.
- Whether the agentic stage pays off is untested where it should matter (Tier 3/4); P4 exists to answer that before P5.
