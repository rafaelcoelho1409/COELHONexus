# DD Synth as an agentic graph (`ai/dd/…/pipeline/synth`), 2026-11-06

Status: built and offline-tested; **not run live** (needs a redeploy of the API, the planner worker, the synth worker and the web together: the
Celery task names and queues changed, and a run in flight dies). Nothing is committed. The old `domains/dd/pipeline/synth/` package is deleted
(it is in git history). Built by Claude at the user's explicit request, a one-time exception to "the user writes `ai/dd/` by hand".

## What changed

The synth was seven sequential LangGraph nodes whose LLM calls were single structured answers wrapped in hand-rolled vote, tournament, repair
and retry loops. It is now a parent graph of the **same seven nodes** (same names, same `SynthState` fields, same progress events and
thread-id prefixes: the web contract is unchanged) where each stage with LLM work is a subgraph, and **every agent is a `create_deep_agent`**.
`book_harmonize` (run by the study loop after the last chapter) is a subgraph too.

| Node | Shape now |
|---|---|
| `outline_sdp` | `prepare` (sources, vault hashes, embeddings for the H2 dedup) → `chapter_outliner` (reads source pages from `/sources/`, checks with `validate_outline`) → `hard_trim`/`flatten` → `finalize` (DAG, stages, cycle repairs); a bad outline goes back to the agent with the issues |
| `digest_construct` | `prepare` (long pages are split in chunks of `CHUNK_MAX_BLOCKS`) → `Send` one `source_router` per chunk (checks with `check_digest`) → `collect` (merge chunks, stubs for failed sources, orphan/over-spread stats) → `finalize` |
| `sawc_write` | `prepare` → stage loop: `Send` one `section_writer` per section of the stage (reads blocks in full from `/vault/`, checks with `check_section`) → `finish_stage` (cycle to the next stage) → coverage top-up (`coverage_extender`) and source top-up (`source_extender`) rounds with a salvage pass → late retry of lost sections → `finalize` |
| `sawc_derive` | `prepare` (thin blocks of every digest source) → `Send` one `code_deriver` per thin block (checks with `check_code`) → `finalize` (promotes into `sawc-latest.json` **and** the iteration's versioned blob) |
| `checklist_eval` | `prepare` (11 deterministic gates) → `chapter_judge` (5 criteria) → second opinions for a hard failure → majority → `finalize`; tracks the best draft seen |
| `mgsr_replan` | `prepare` → `chapter_reviewer` (inspects failing sections with `inspect_section`, reads its skill) → `finalize`; the router halts or loops |
| `render_audit_write` | `prepare` → `Send` `code_normalizer` per mangled python block (cached by content hash) → `finalize` (renders the **best-seen** draft, audits) |

The loop is the old CoRefine one: `route_after_mgsr` halts on success, no recovery, a sustained judge outage, the iteration budget
(`MAX_REFINE_ITER = 5`), a plateau, a reviewer halt or the wall-clock gate. The difference is that the reviewer is a real agent: it names the sections to rewrite
(`decision.revisions`) and `sawc_write` in revise mode rewrites **only those** and reuses the rest from the previous iteration's blob.

### The agents

| Agent | Deep Agents features used |
|---|---|
| `chapter_outliner` | skill `design-chapter-outline`; read-only `/sources/` backend (`FilesBackend`); `validate_outline` tool with a `ToolCallLimit` |
| `source_router`, `section_writer` | `response_format`; `check_digest` / `check_section` tools (the agent proposes, deterministic code decides); the writer's `/vault/` backend holds each routed block in full |
| `coverage_extender`, `source_extender`, `chapter_judge`, `claim_extractor`, `term_canonicalizer`, `paragraph_patcher` | `response_format`, rubric in `SYSTEM.md` (many calls a run: no skill) |
| `code_deriver`, `code_normalizer` | `check_code` tool (the same `python_ast_valid` the node gates on) |
| `chapter_reviewer` | skill `review-failed-chapter`; `inspect_section` tool |
| `drift_detector` | skill `audit-chapter-consistency`; read-only `/chapters/` backend (`grep` the sibling chapters before reporting) |

Shared factory: `ai/dd/agents/service.py::build` / `ask`, same as the planner. New in this change: `ai/dd/backends/files` (a read-only in-memory
`BackendProtocol`, `mounted(files, route)` builds the `CompositeBackend`), and `SynthContext.scratch` (data shared across the branches of a
run: vault, digest, completed sections; every stage's `prepare` rebuilds it, which is what makes resume work).

## Where things are

```
ai/dd/agents/pipeline/synth/<role>/{agent,config}.py       + schemas.py (lenient answer models), params.py
ai/dd/prompts/pipeline/synth/<role>/{prompts.py,SYSTEM.md}
ai/dd/skills/pipeline/synth/{chapter-outliner,chapter-reviewer,drift-detector}/<skill>/SKILL.md
ai/dd/tools/pipeline/synth/{check,view}/service.py          validate_outline, check_digest, check_section, check_code, inspect_section
ai/dd/backends/files/                                        the read-only files backend
ai/dd/graphs/pipeline/synth/{graph,state,context,params,router}.py   parent graph, SynthState, SynthContext/SynthAgents, loop routing
ai/dd/graphs/pipeline/synth/<node>/{graph,state}.py          the subgraphs (+ book_harmonize)
ai/dd/graphs/pipeline/synth/{service,study}.py               a chapter's run/resume and its end (metrics, usage, timing); the study loop (strict order, skip of renders, cancel, timing roll-up)
ai/dd/graphs/pipeline/runner/{entities,service}.py           shared with the planner: Binding, drive() over astream, launch with the cancel watcher, settle
ai/dd/nodes/pipeline/synth/<node>/{node,service,domain,params,keys,schemas,versions,patterns}.py   shared/ holds what two nodes use
ai/dd/nodes/pipeline/synth/book_harmonize/node.py            run_pass: the study's pass (load READMEs, cache, harmonize, overwrite patches)
domains/dd/pipeline/ai/synth/service.py                      the binding only: the graph over the shared checkpointer, MinIO, a context per run; delegates to the above
domains/dd/pipeline/ai/task.py                               run_single_chapter, resume_synth, run_study (synth queue) + the planner's tasks
domains/dd/ingestion/{vault,corpus_normalize,backfill}/      moved from synth: ingestion uses them too
```

`ai/` never imports `infra`, so a run is handed a `Binding` (compiled graph over the shared checkpointer, object storage, a context factory, the thread's
config) built in `domains/dd/pipeline/ai`. What `ai/` needs from Redis, Langfuse and OpenTelemetry goes through `domains/dd/pipeline/{runtime,observability}`
(`cancel.service.{reset,requested}`, `dispatch.service.{track_chapter,untrack_chapter,clear_active_study}`, `observability.service.workflow_span`).
`study_sem()` (env `DD_STUDY_SEM`) lives in `graphs/pipeline/synth/params.py`; the 2-chapter minimum of the harmonize pass in `book_harmonize/params.py`.

## Behaviour that changed on purpose

- No vote / tournament / critic-picker: `outline_sdp` is one agent with a validation tool, `sawc_write` one writer run per section (a second
  run only when a hard issue remains), `sawc_derive` one deriver run per block. `n_critic_picks` and `n_picker_fallbacks` are always 0;
  `n_samples` on `outline_stats` counts agent runs.
- The checklist lost the picker gate, CoCoA and the atomic criteria: **11 deterministic gates + 5 LLM criteria**. A hard LLM failure gets
  second opinions; a judge outage scores the chapter on its gates and is flagged `infra_degraded` (the chapter is not rewritten for it).
- `mgsr_replan` stats: `n_actions` became `n_revisions`; `halt_reason` is one of `chapter_passed`, `reviewer_unavailable`, `reviewer_halt`,
  `targeted_revision`, `full_rewrite`. The router's own halts (no-recovery, sustained outage, budget, plateau, wall clock) are only logged, as before.
- Langfuse prompt overrides (`dd.synth.*`) no longer apply: the prompts are `SYSTEM.md` files. The span names (`dd.synth.*`) are unchanged.
- Skills only on `chapter_outliner`, `chapter_reviewer`, `drift_detector`: the per-section and per-block roles run hundreds of times.
- Cached node blobs miss once (`PROMPT_VERSION` bumped in every synth node).
- Celery: the three synth tasks are `domains.dd.pipeline.ai.task.{run_single_chapter,resume_synth,run_study}` (route → synth queue); the rest of
  the module is the planner's (→ planner queue). Redis keys, MinIO prefixes and thread-id prefixes did not change.
- Removed: `domains/dd/pipeline/synth/` and the service-level LLM checks of `scripts/dd/test_synth_fixes_1..7.py` (the deterministic checks stay,
  repointed; the pipeline is covered by the two scripts below).

## Tests

- `scripts/server/test_dd_synth_graph.py`: outline (flawed outline retried, semantic dedup), digest (stubs, chunks), a full chapter, the
  refine loop (targeted rewrite, reviewer halt, reviewer down), coverage/source top-ups with salvage, writer retries and late retry,
  judge outage, derive (promote/reject) and render normalize (cache), resume, `book_harmonize`.
- `scripts/server/test_dd_pipeline_runs.py`: how graphs are run, on a small real LangGraph over an in-memory checkpointer with the Redis plumbing
  replaced by recorders: the planner's and a chapter's run, failure, cancel and resume; the study (skip of rendered and stale chapters, needs_review, cancel,
  all-failed, harmonize crash, timing roll-up); the harmonize pass (skip, overwrite, cache hit, crash).
- `scripts/server/test_dd_synth_agents.py`: each real agent with a scripted model: skills parse, permissions refuse paths outside the mount,
  tools run and report, backends serve files read-only.
- Unchanged and green: `test_dd_routers.py` (golden), `test_api_layout.py`, `test_infra_boundary.py` (`ai-no-infra`), `export_openapi --check`,
  `apps/web` typecheck / vitest (259) / contract:check / api:check / knip.

## Not verified, to watch on the first live run

- Tool-calling reliability of the free pool for the outliner, router, writer and reviewer (bounded by `ModelCallLimit`/`ToolCallLimit`, with
  deterministic fallbacks, but only a live run shows it).
- The cost of a skill read on the outliner and reviewer (one extra turn per call); the writer's `/vault/` reads per section.
- Whether the reviewer's targeted rewrites converge faster than the old whole-chapter loop (watch `n_revisions` and `halt_reason`).
- `apps/web` Playwright (e2e) was not run for this change; `copy:check` fails on `src/features/settings/params.ts:19` (`fgts-va`), which predates it.
