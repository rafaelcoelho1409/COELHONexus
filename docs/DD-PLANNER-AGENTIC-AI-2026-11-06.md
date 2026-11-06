# DD Planner as an agentic graph (`ai/dd/…/pipeline/planner`), 2026-11-06

Status: built and offline-tested; **not run live** (needs a redeploy of the API, the planner worker and the web together: the Celery task
names changed). Nothing is committed. The old `domains/dd/pipeline/planner/` package is deleted (it is in git history).

## What changed

The planner was eight sequential LangGraph nodes whose LLM calls were single structured answers wrapped in hand-rolled retry, repair and
fallback loops. It is now a parent graph of the same eight nodes (same names, same `PlannerState` fields, same progress events: the web is
unchanged in contract) where each stage with LLM work is a subgraph, and **every agent is a `create_deep_agent`**.

| Stage | Shape now |
|---|---|
| `corpus_load`, `plan_write` | deterministic nodes |
| `off_topic` | `prepare` (dedupe identical page texts, auto-keep reference sub-pages, load stored verdicts) → `Send` one `page_judge` per distinct text → `tally` → a second look at big drops → `finalize` |
| `doc_distill` | `prepare` → `Send` one `page_distiller` per page (second try with half the body, then a deterministic fallback) → `finalize` |
| `chapter_propose` | `prepare` → `Send` one `section_analyst` per URL directory (corpora of 120+ pages) → `chapter_architect` → **score gate** (cycle: a weak outline goes back once with the uncovered pages) → `finalize` (best outline, else the seed fallback) |
| `chapter_assign` | `prepare` (builds the `page_scorer` with the run's chapters in its system prompt) → `Send` one scorer per page, each running a ladder (ask → complete missing chapters → all-zero re-ask with the closest-chapter line → lexical fallback) → `tally` → sweep over the fallbacks → `finalize` |
| `chapter_select` | `prepare` (the deterministic selection, 1,400 lines, unchanged) → `Send` one `chapter_curator` per oversize chapter → `apply` (cycle for parts still over the code-block cap) → `finalize` |
| `order_chapters` | `prepare` → `Send` 5 `chapter_sequencer` samples → `collect` (cycle: one top-up round for a thin batch) → Borda + pins |

### The agents

| Agent | Deep Agents features used |
|---|---|
| `page_judge`, `page_distiller`, `page_scorer` | `response_format`; rubric in `SYSTEM.md` (hundreds of calls a run: no skill, no extra turn); the scorer's schema is built per run (`c0..cN`) |
| `section_analyst`, `chapter_sequencer` | `response_format` + a skill read through `/skills/**` |
| `chapter_architect` | skill; `write_todos`; a **read-only `/corpus/` backend** (`CompositeBackend` over a MinIO-backed `BackendProtocol`: `ls`/`glob`/`grep` on summaries, `read_file` on page text); the `score_outline` tool; the `plan_critic` **subagent** (two lenses, sent in parallel); `ToolCallLimit` on the score tool |
| `chapter_curator` | skill; tools `cluster_units` and `validate_split` (the agent proposes, deterministic code validates) |

Shared by all of them (`ai/dd/agents/service.py`): model from `settings.chat` (Rotator task per role; the scorer keeps `dd.assign`), harness
profile with only the tools the role needs, file permissions limited to `/skills/**` plus the role's own paths, `ModelRetry` for transient
errors, `CapacityWait` (the Rotator's "no usable arm", ported from `settings.chat`), `ModelCallLimit`, `ToolCallLimit`, `ToolError`, the
unreviewed-tool guard, `LLMCounterCallback` on every call, Rotator request id read from the response headers for `report_quality`.

## Design rules taken from `AIAgentsStudies`

- A step that must happen is a **node**; a step the model decides is a **tool inside the agent**; behaviour on every model call is
  **middleware** (`CapacityWaitMiddleware`, the resilience stack).
- Nodes are **idempotent**: LangGraph re-runs an interrupted node from its start. A spike on the pinned versions (langgraph 1.2.16) showed a
  failed `Send` fan-out re-runs *every* branch on resume, so resume is carried by **per-page results in MinIO** (content-addressed under
  `dd/{slug}/planner/items/<node>/<version>/<digest>.json`), as ingestion does with its per-page artifacts. A re-plan of an unchanged corpus
  also calls no model.
- State holds small JSON-able values; page text never enters a checkpoint. Data the parallel branches share (the run's chapters, the
  distillates) is loaded once in `prepare` into the run context's `scratch`, which a resume rebuilds.
- Progress is `get_stream_writer()` custom events (they reach the parent stream from `Send` workers with `subgraphs=True`); the run shell
  republishes them on the Redis channel in the existing shape.
- Nothing under `ai/` imports `infra` (guard `ai-no-infra`); `domains/dd/pipeline/ai` is the composition root.

## Where things are

```
ai/dd/agents/{service,entities,params,domain}.py            the factory, ask(), Role/ModelConfig
ai/dd/agents/pipeline/planner/<role>/{agent,config,params}  + schemas.py, params.py (the answers' limits)
ai/dd/prompts/pipeline/planner/<role>/{prompts.py,SYSTEM.md}
ai/dd/skills/pipeline/planner/<role>/<skill>/SKILL.md       (architect also has references/rubric.md)
ai/dd/tools/pipeline/planner/{outline,split}/service.py     score_outline, cluster_units, validate_split
ai/dd/backends/corpus/                                       the read-only /corpus/ filesystem
ai/dd/middleware/service.py                                  CapacityWaitMiddleware
ai/dd/graphs/pipeline/planner/{graph,state,context}.py       parent graph, PlannerState, PlannerContext/PlannerAgents
ai/dd/graphs/pipeline/planner/<stage>/{graph,state}.py       the subgraphs
ai/dd/nodes/pipeline/planner/<stage>/{node,service,domain,params,versions}.py
domains/dd/pipeline/{ai,runtime,storage,observability}/      composition root + Celery tasks, Redis plumbing, MinIO keys, spans
```

## Behaviour that changed on purpose

- `chapter_propose` no longer samples 5 drafts and votes; it is the analysts + architect + score gate above. `propose_stats` gained
  `n_sections`, `n_sketches_valid`, `architect_rounds`, `score`, `unmatched_pct`, `largest_share_pct`; `n_samples_valid` is now 0 (fallback) or 1.
- `chapter_select`'s two split paths (cluster-then-name, model partition) are one curator run; the deterministic cluster naming remains
  as its fallback.
- The Langfuse prompt overrides (`dd.planner.chapter_propose`, `dd.planner.chapter_assign`, ...) no longer apply: the prompts are `SYSTEM.md`
  files. `scripts/observability/publish_chapter_propose_prompt.py` and `run_faithfulness_eval.py` still target the old template.
- Cached node blobs miss once (`PROMPT_VERSION` bumped in `doc_distill`, `chapter_propose`, `chapter_assign`, `chapter_select`, `order_chapters`;
  `off_topic` has its own).
- The resume "catch-up" path (re-running nodes added after a thread finished) is gone: the graph is fixed.
- Removed one-off scripts that tested the old LLM code: `scripts/dd/test_planner_{batch,fixes_2..7,fixes_9,run_5}.py`. The checks that were
  about deterministic logic stay (`test_planner_fixes_8/10/11`); the pipeline is covered by `scripts/server/test_dd_planner_graph.py`
  (full run, re-plan, resume of a failed page, large corpus, assign ladder + sweep, score gate, curator split, order top-up, dead-architect
  fallback) and `test_dd_planner_agents.py` (each real agent with a scripted model, skills, permissions, backend, tools).

## Not verified, to watch on the first live run

- Tool-calling reliability of the free pool for the architect, the critic and the curator (bounded by `ModelCallLimit` and
  `ToolCallLimit`, with deterministic fallbacks, but only a live run shows it). `structured_or_recovered` covers models that answer with fenced JSON.
- The cost of a skill read on `section_analyst` and `chapter_sequencer` (one extra turn per call).
- `PlanHealth` and the Langfuse trace of a 300+ page framework: section count, architect rounds, score.
- `RubricMiddleware` (beta) is deliberately not used: the score gate is deterministic and cheaper.

## Ideas left out (for later)

Deep Agents `MemoryMiddleware` + a `StoreBackend` (`AGENTS.md` per framework: stable chapter identities across re-plans, which would keep
synth's per-chapter caches valid); `interrupt_on` / `interrupt()` for a human review of a degraded plan before `plan_write`.
