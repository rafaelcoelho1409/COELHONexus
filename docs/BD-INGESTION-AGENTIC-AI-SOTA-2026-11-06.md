# BD agentic ingestion — SOTA review of agents, prompts, skills, tools and the deterministic code (2026-11-06)

Scope: `apps/server/ai/bd`. Question: how to make the agents work together best, and which deterministic code should become prompts/skills/tools/MCP and which should stay. The review below was written first; **"What was built (workflow 1.12)" at the end records what was implemented from it** and where it deviated. Offline-tested, not deployed, nothing committed.

## Decision

1. **Keep the LangGraph workflow and the deterministic gates; move the agents' *judgement* inputs and the regex noise-filters.** Anthropic's own guidance is to use workflows (fixed code paths) for well-defined tasks and add agent autonomy only where it "demonstrably improves outcomes"; the BD pipeline is a prompt-chain plus a deterministic router, which is the right shape. Do **not** add a supervisor LLM or dynamic subagents.
2. **Add tools, not MCP.** Give the specialists small in-process LangChain tools (a patch validator, adjacent-page edges, a whole-book term-frequency lookup). MCP buys nothing for pure functions inside one process.
3. **Replace only one class of deterministic code:** the regexes over the auditor's *free-text descriptions* (`SELF_DISMISSED_RE`, `IMAGE_PLACEMENT_RE`). They grow one run at a time (1.5 → 1.11) and cannot generalise. Replace them with a restricted schema plus a verifier pass.
4. **Everything that checks an LLM edit against the source stays in code.** That is the evidence the field has for over-correction (below), and it is what made 1.10 "no wrong auto-applied repair".

## What the deterministic code does, node by node

| Node | Deterministic code applied to the extracted pages | Verdict | Why |
|---|---|---|---|
| `inventory` | `assess_page`: page kind from picture count; control characters, soft hyphens, near-empty, dotted leaders, malformed table rows, fragmented uppercase headings; `merge_issues`; `effective_length` | **Keep** | Character-level facts, exact, free, 100% precision. Pass its findings *into* the auditor payload so the model stops re-reporting them (today they are merged after the fact). |
| `audit_batch` | fan-out with `asyncio.Semaphore`, retries, fallback to deterministic findings when the auditor fails, `_gather_cancel_on_failure` | **Keep, restructure** | Orchestration. Make each page a durable LangGraph task (`Send` + checkpoint) so one failure does not discard finished pages (the DD Locust run lost 23 good audits to one 504; see `docs/DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05.md`). |
| `page_audit` — evidence checks | `sanitize_assessment`: excerpt must be an exact substring; per-code evidence rules (`_evidence_supports_issue_code`); `NEAR_EMPTY`/`METADATA` always dropped | **Keep; move two rules into the schema** | Grounding gate. Dropping codes the auditor must never emit belongs in the response schema enum (make the mistake impossible) rather than a post-hoc filter. |
| `page_audit` — description regexes | `SELF_DISMISSED_RE`, `IMAGE_PLACEMENT_RE` discard findings whose *description* says "typical for…", "mix of text and an image…" | **Replace** | This is judgement over free text. Use a schema field (e.g. `verdict: defect | not_a_defect`, required) plus a cheap verifier call on surviving findings of low confidence or `structure`/`other` code (evaluator-optimizer: "clear evaluation criteria… iterative refinement provides measurable value"). Keep the regexes only as a regression test of the verifier. |
| `plan_repairs` | issue code → specialist role lookup | **Keep** | A pure table. Anthropic's routing/orchestrator-workers split: use an LLM orchestrator only "where you can't predict the subtasks"; here the subtasks are fixed by the issue code. |
| `repair_pages` — gates | `validate_proposals` (target present, unique, inside supplied context); `_invents_text`; `_is_spacing_repair` (only spaces differ, no URL/path, no letter change, no new hyphen from a soft hyphen); `_is_auto_applicable` | **Keep** — and expose as a tool | These are what stopped the four wrong suggestions (`ACKNOWLEDGEMENTS`, `## Chapter 6`, `Nation-state`, `VirusTotal -Intelligence`) at confidence 0.9–1.0. Calling the same check *inside* the specialist (validator-as-tool) replaces the outer "previous_attempt_rejected" retry loop (up to 3 full re-invocations per work item). |
| `repair_pages` — mechanical | strip control chars/soft hyphens; table normalisation and TOC-leader removal on every page | **Keep control/soft hyphen in code. Move table/TOC rewriting behind a tool** | The first is unambiguous Unicode. The second is a heuristic content rewrite that runs without any model or issue check. Offer it as a tool to the structure specialist whose result passes the same diff gate, so it only fires when a page was flagged. |
| `repair_pages` — context | `build_repair_context` windows around evidence | **Keep** | Bounds tokens before the call. Add tools for what the window cannot show (below). |
| `*_domain` JSON recovery | `_json_objects`, `_last_assistant_content` copied into 3 domains | **Replace with the framework** | `create_deep_agent(response_format=…)` already returns `structured_response`; use one shared fallback or the structured-output retry middleware instead of three copies. Needs `langchain>=1.4.9` for `ModelRefusalMiddleware` (changelog). Verify the current `ToolStrategy` error-handling options before dropping the fallback. |
| `metadata_normalization` | evidence-in-front-matter check; `_metadata_value_is_grounded` token subset; filename/site-tag fallback title | **Keep** | The fallback must not depend on the model; the grounding check is the guard. |
| `publish` | blank discard, delivery disposition, hashing, immutable writes, quality report (not read line by line) | **Keep** | Policy and integrity, not judgement. |

## Tools the agents should have (all in-process)

Anthropic's tool rules: few tools, consolidated by workflow, high-signal responses, actionable errors, unambiguous parameter names. Deep Agents: give each subagent only the tools it needs.

1. `check_patch(target_text, replacement)` — the existing `_is_spacing_repair` / `_invents_text` / uniqueness gate, returning `{ok, reason}`. Specialists call it before answering. *Replaces the outer retry loop.*
2. `book_term_frequency(variants: list[str])` — counts across all raw pages (`cyberattack` 57, `cyber-attack` 12, `cyber attack` 10 was computed by hand in the 1.10 assessment). Gives the model corpus evidence for hyphen/compound decisions instead of a guess. Build the index once per run from the raw pages.
3. `adjacent_page_edges(page, lines=3)` — last/first lines of the neighbouring pages. Targets the "paragraph continues on the next page" and "footnote number fused into prose" findings (pages 150, 163, 194) that the auditor is currently forbidden to judge ("no evidence from another page").
4. Optional, later: `view_page_image(page)` for the judgement-call pages. Deep Agents' `read_file` handles images/PDF since v0.5; BD would need on-demand rasterisation of the stored source PDF and a vision-capable model.

## Prompt and skill structure

- **One source of truth per rule.** `page_auditor/SYSTEM.md` and `audit-docling-page/SKILL.md` repeat the same sentences (`<!-- image -->`, `&amp;`, mid-sentence pages, `toc_noise`). That doubles tokens and lets them drift. Put identity, output contract and safety in the system prompt; put the procedure in the skill; keep the growing "never a finding" list in one file.
- **Skills are for optional or large material.** Deep Agents injects only a skill's `name` and `description`; the body costs a `read_file` round trip. Each BD agent has exactly one skill it always needs, and the 1.10 run made 464 audit calls for about 262 audited pages (inference, not traced). If that extra call is the skill read, inline the procedure in the system prompt and keep skills for shared material (for example a `false-positives.md` reference) that is loaded on demand.
- **Skill rules (official):** `name` 1–64 lowercase/hyphen and equal to the directory; `description` ≤ 1,024 characters, third person, what + when; body < 500 lines / ~5,000 tokens; references one level deep; deterministic steps as scripts ("Run X" vs "See X"); a validator loop (run → fix → repeat). Pass skills as a directory of skill directories.
- **System prompt skeleton** (official guidance is example-driven, no mandatory template): role + one-line mission → untrusted-input statement → decision procedure → hard rules → output contract. Subagent/agent `description` should be "specific and action-oriented".
- **Evaluate before editing prompts** (Anthropic skill guidance): freeze the 99 verified repairs, the 4 refused suggestions and the 22 review pages from the 1.10 assessment as a replayable set, and run it on every prompt or model change. The model pool changes each run, so counts alone cannot show a regression.

## Coordination

- Outer loop: LangGraph workflow, as now. Worker agents inside nodes (one page, one issue group), isolated by construction — Deep Agents' "context quarantine" is already achieved per call, so subagents add nothing ("simple, single-step tasks… when the overhead outweighs benefits").
- Hand-off through structured artifacts in MinIO (`assessment.json`, `patch-proposals.json`) — matches Anthropic's "filesystem as memory". Keep Markdown out of graph state, as now.
- LangGraph 1.2 adds per-node timeouts and error recovery; use them instead of the hand-written `_gather_cancel_on_failure` where it fits.

## What `AIAgentsStudies` adds (chapters 001–010, AgenticMath)

Read from `~/Workbench/AIAgentsStudies/deepagents/*` and `AgenticMath/` (summary by a read-only agent; paths relative to `deepagents/`).

- **Same conclusion as the web sources, from your own experiments.** `docs/agenticmath-architecture.md`: a step that must happen is a **graph node**, a step the model decides is a **tool inside the agent**, a behaviour on every call is **middleware**. Chapter 008's first run had the coordinator do the work itself and reply without a tool call (which ends a Deep Agents run); AgenticMath replaced it with a LangGraph `StateGraph`. BD already follows this; do not go back to a coordinator.
- **Programs give PASS/FAIL, agents give flags.** 008's `scripts/check_modules.py`, `trace_numbers.py` and `domains/verification` (pristine copy, held-out seeds) run before and after the critic; "a check that cannot fail is not evidence". This is the model for the BD gates: `check_patch` as a tool for the specialist, plus a final deterministic verifier node (page count, no empty page, no text loss beyond a threshold) that produces the quality report, not an agent.
- **Per-page durability.** AgenticMath uses `AsyncPostgresSaver` with `durability="sync"`, idempotent nodes and resume by `thread_id` when `snapshot.next` is set; `Send` fan-out is designed there but not built. BD can build it first: one task per page, skip pages whose artifact exists.
- **One file per page, never a shared file.** 009: concurrent `write_file` on one file was last-write-wins (140 of 160 facts lost); per-topic files lost none. BD already writes one artifact per page.
- **Guards as middleware.** 008's `WriteGuardMiddleware` (refuses `write_file`/`edit_file` outside a role's paths) and `tools=[]` on dict subagents to block inheritance; 010's `ToolErrorMiddleware` outside `ToolRetryMiddleware`, `ToolCallLimit`/`ModelCallLimit`, and the `page-reader` subagent as a context firewall. BD uses `ModelCallLimitMiddleware(run_limit=4)` and `FilesystemPermission` deny rules already; add tool-error sanitising once tools exist.
- **Images.** 004: a `HumanMessage` whose content list holds an image block (base64 for local files) with a vision-capable model — the route for the optional `view_page_image` tool.
- **Prompt shape you used.** XML-style sections (`<input>`, `<grades>`, `<how>`, `<reply>`), a small fixed taxonomy, an explicit reply cap, "when unsure choose unverified", untrusted-data statement, and a `DESCRIPTION` written as a contract for the caller. The BD prompts are close; the missing parts are the reply cap and the "unverified" default.
- **Gap in the studies.** `response_format=` / `ToolStrategy` is not demonstrated in any chapter (010 uses `with_structured_output` only for graders). BD's use of `response_format=` and its JSON-recovery fallback are therefore not covered by your own notes; add a short chapter or test before removing the fallback.

## Options compared

| Approach | Maturity | Versions (date) | Trade-offs | Pick when |
|---|---|---|---|---|
| A. Current: workflow + agents + deterministic gates | proven on 5 live runs | deepagents ≥0.6 pinned; latest 0.7.0 (Jul 24, 2026); langgraph 1.2.0 (May 12, 2026) | Accurate and cheap; judgement inputs thin; regex filters grow per run; up to 3 re-invocations per rejected patch | Default if nothing changes |
| **B. A + tools (validator, term frequency, adjacent pages) + verifier + schema limits** | in-framework features only | same | +1 cheap verifier call on a few findings; fewer retries; removes the regex growth | **Recommended** |
| C. Supervisor deep agent with subagents per issue type | newer; subagents are stateless, report back once | deepagents 0.7.x | LLM routing for a table lookup; more calls, less predictable | Only if issue types become unpredictable |
| D. Skills with scripts run in a sandbox | documented pattern | deepagents sandboxes | Needs a sandbox backend and its ops; same checks as B tools | If scripts must be shared across agents/languages |

## Evidence

- Workflows vs agents, "simplest solution possible", evaluator-optimizer, orchestrator-workers, routing — Anthropic, *Building effective agents*, https://www.anthropic.com/engineering/building-effective-agents (Dec 19, 2024; page notes tooling has since changed).
- Tool design: fewer/consolidated tools, high-signal responses, actionable errors, parameter naming, evals — Anthropic, *Writing effective tools for agents*, https://anthropic.com/engineering/writing-tools-for-agents (Sep 11, 2025).
- Skill authoring rules, scripts for deterministic operations, validator loops, "evaluations BEFORE documentation" — Anthropic Agent Skills best practices, https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (undated); open spec https://agentskills.io/specification (undated).
- Filesystem as memory, progressive disclosure of tools — Anthropic, *Code execution with MCP*, https://www.anthropic.com/engineering/code-execution-with-mcp (Nov 4, 2025).
- Deep Agents skills (progressive disclosure, SkillsMiddleware, limits, "bundle procedures with scripts"), subagents (context quarantine, when not to use), structured output — LangChain docs, https://docs.langchain.com/oss/python/deepagents/skills, …/deepagents/subagents, …/langchain/structured-output (undated).
- Versions and breaking changes (deepagents 0.5.0 Apr 7, 0.6.0 May 12, 0.7.0 Jul 24 2026; langgraph 1.2.0 May 12; langchain 1.4.0 Sep 1 replaced `langchain-mcp-adapters` with `langchain.mcp`; 1.4.6 Oct 14) — https://docs.langchain.com/oss/python/releases/changelog (entries through Oct 22, 2026).
- LLM over-correction is a recurring risk, guards and length-ratio fallback used by teams, no ablation of damage rate — Ehrmann et al., ICDAR 2026 HIPE-OCRepair, https://arxiv.org/abs/2607.08143 (Jul 9, 2026).
- Deterministic parsing around an LLM mainly reduces cost/latency; accuracy was equal on most formats — Wang & Shen, https://arxiv.org/abs/2510.10138 (Oct 11, 2025).
- docling-mcp v2.0 targets conversion/document generation — https://pypi.org/project/docling-mcp/2.0.1/ (no 2026 release date found).
- FastMCP 3: in-process server passed directly as a toolset, decorated functions stay plain callables — https://gofastmcp.com/v3/integrations/pydantic-ai; project pins `fastmcp>=3.4.10`.

## Caveats and unknowns

- LangChain docs pages carry no dates; "latest" patch versions (deepagents ≥0.7.23, langchain ≥1.4.9) are inferred from version notes, not from a release list.
- No source quantifies how often an LLM damages clean text, and none ablates deterministic guards. The case for keeping the gates rests on the BD runs themselves (four wrong suggestions refused at confidence ≥0.9) plus the HIPE-OCRepair qualitative finding.
- The skill-read round-trip explanation for 464 audit calls is an inference; check a trace before acting on it.
- Multi-agent hand-off guidance in the fetched Anthropic pages is thin; the coordination section leans on Deep Agents docs and the existing BD design.
- `publish/service.py` was classified from its role, not read in full.
- The `AIAgentsStudies` summary comes from a read-only agent that read all ten chapters; I did not re-open each file.

## Suggested order

1. `check_patch` tool + drop the outer retry loop (fewer calls, same safety).
2. Schema restriction (no `near_empty`/`metadata` from the auditor, required `verdict`) + verifier pass; then retire the two description regexes.
3. `adjacent_page_edges` and `book_term_frequency` tools.
4. Per-page durable fan-out (`Send` + checkpoint) and a final deterministic verifier node.
5. De-duplicate prompt/skill text; decide inline-procedure vs skill from a trace.
6. Freeze the 1.10 evidence set as a replay eval before touching prompts.
7. Later, if needed: page-image tool; upgrade `langchain` to ≥1.4 (`langchain.mcp` replaces `langchain-mcp-adapters`).

## What was built (workflow 1.12)

Run all of it with `cd apps/server && for t in test_bd_agent_schemas test_bd_page_auditor test_bd_page_auditor_scoring test_bd_inventory_graph test_bd_ingestion_full_workflow test_bd_ingestion_ai_service test_bd_ingestion_ai_task test_bd_replay_eval test_bd_specialist_tools test_bd_finding_verifier; do .venv/bin/python ../../scripts/server/$t.py; done`. All pass; none of it has run against a model, so the call savings below are expectations, not measurements.

| # | Step | Where | Result |
|---|---|---|---|
| 1 | Replay eval | `scripts/server/test_bd_replay_eval.py`, fixture `scripts/server/data/bd_replay_workflow_1_10.json.gz` (64 pages, 112 proposals), exporter `scripts/server/export_bd_replay_fixture.py` | Replays the 1.10 run's stored proposals through the current gates. 99 were applied in 1.10, 98 are now; the only difference is page 37 (soft hyphen stays soft); the four wrong suggestions (pages 15, 155, 173, 259) stay out. |
| 2 | Profile isolation | `ai/bd/agents/service.py` `isolate_harness_profile`, `ai/bd/middleware/service.py` `ToolAllowlistMiddleware`, all five roles | Mirrors the Docs Distiller fix. The shared model profile now excludes nothing; each role is limited by its own allowlist. A test leaks an exclusion under the same model name and checks each role still sees exactly its tools. |
| 3 | `check_patch` | `ai/bd/tools/specialist.py`, `repair_pages/domain.py` `check_draft`, per-call `SpecialistContext` passed in `config["configurable"]` | Same checks as the final gate (`validate_proposals`, spacing/mechanical rules), so the tool and the gate cannot disagree. Specialist model-call limit 8, `SPECIALIST_ATTEMPTS` 3 → 2. |
| 4 | Auditor schema + verifier | `agents/schemas.py` (`AuditIssueCode` without `near_empty`/`metadata`, required `verdict`), new `finding_verifier` agent + prompt, `nodes/ingestion/finding_verification/`, wired in `audit_batch` | A finding the auditor marks `not_a_defect` is dropped; `structure`/`other` findings and those under 0.8 confidence get a second reader. A failed or unreadable verifier keeps every finding. |
| 5 | Context tools | `book_term_frequency`, `adjacent_page_edges` (text repair); `adjacent_page_edges` (structure repair); `BookIndex` reads the book once, lazily | Whole-word, case-insensitive counts across line breaks and soft hyphens. |
| 8 | Publication check | `nodes/ingestion/publish/domain.py`, `service.py` `verify_pages` | Re-reads source and output for every delivered page: a hash mismatch stops publication; a loss of more than max(12, 1%) letters and digits marks the run `complete_with_review`. The result is in `quality-report.json` under `verification`. No false positive on the 64 replayed pages. |
| 9 | Prompts / skills | `page_auditor/SYSTEM.md`, auditor `SKILL.md` (1.3), text/structure repair `SYSTEM.md`, new `finding_verifier/SYSTEM.md` | Auditor skill no longer repeats the system prompt's rules; tools and `verdict` are described. |
| UI | Usage labels | `apps/web/src/features/bd/domain/agentic-usage.ts` | `finding_verifier` counted under "Audit pages". |

`WORKFLOW_VERSION` is `1.12`.

### Not done, and why

- **Step 6, table/TOC cleanup as a tool.** On reading the code again this would move fixes that today are applied by code (15 mechanical fixes in the 1.10 run) into review, because a tool-assisted patch is not auto-applicable, and it would add model calls. It stays in code. A better change is to scope it to flagged pages and measure the effect on a real run first.
- **Step 7, per-page `Send` fan-out.** BD already resumes at node level through the Postgres checkpointer, and page-level model failures degrade to deterministic findings instead of raising, so the Docs Distiller failure (one 504 aborting the node) does not occur here. The restructuring is large for what it would add; do it only if an interrupted run (redeploy mid-node) proves costly. A cheaper first step is reusing a page's stored assessment and patch-proposals when the source hash matches.
- **Step 8 as its own graph node.** Folded into `publish` instead, so the pipeline's node list, progress events and the web graph stay unchanged.
- **Removing the two description regexes** (`SELF_DISMISSED_RE`, `IMAGE_PLACEMENT_RE`). Kept as a cheap pre-filter until the `verdict` field and the verifier are measured on a real run; delete them after that run if the review-page count does not rise.

### Layout after the cleanup (same day)

`ai/bd/nodes/` now holds only the five graph nodes, and the paths in the table above moved accordingly:
- `finding_verification/` and `page_audit/` were folded into `nodes/ingestion/audit_batch/` (`domain.py`, `params.py`, and `verify_findings` in `service.py`).
- `inventory/` moved to `domains/bd/ingestion/ai/inventory/` (`domain.py`, `patterns.py`, `params.py`), together with `build_repair_context`; `ExtractionRequest` moved to `domains/bd/ingestion/ai/schemas.py`.
- The graph's node adapters live in `graphs/ingestion/service.py`, its dependencies and item shapes in `graphs/ingestion/entities.py`; `graphs/ingestion/state.py` holds only `IngestionRunState`.
- The single-page audit graph, the inventory graph, `WorkPlan`, `ReviewDecision` and `ReviewDisposition` were deleted.

### Check on the next run

Review pages ≈ 19 or fewer (1.11 target); calls per repaired page lower than 1.10's 107 text-repair calls; no use of `check_patch` that ends in a rejected patch left in the answer; `finding_verifier` calls and how many findings it dismisses; `verification.status` `passed`; and, per the first review, the model pool recorded next to the counts.
