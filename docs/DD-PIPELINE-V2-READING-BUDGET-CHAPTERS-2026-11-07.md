# DD Pipeline v2 — reading-budget chapters: research and target architecture (2026-11-07)

Status: **a proposal, nothing built.** It came from a design discussion on 2026-11-07 and a targeted web search (sota-search). The current Planner and Synth (`DD-PLANNER-AGENTIC-AI-2026-11-06.md`, `DD-SYNTH-AGENTIC-AI-2026-11-06.md`) are built and offline-tested but **have never run live**, so their cost and quality are unmeasured; step 0 of the migration is to measure them.

## The goal, as the user stated it

Docs Distiller exists to let the user **read as fast as possible without losing learning quality**: the optimal number of chapters, each one as objective as possible, teaching the best code usage and applications. Not a summary of the docs: a short path to being able to use them.

## The idea under evaluation

1. For every doc, do not filter and tag page by page first. Look at all the pages, find the best grouping, and so decide how many chapters there are.
2. Then synthesize each chapter from all the pages of its group, combining the best code into one step-by-step, runnable, learn-by-doing chapter, in one run.
3. The model for step 2 is a Codex-CLI prompt the user wrote for every LangGraph chapter (`langgraph/002_checkpointers`): read the whole docs URL; take the code in `deepagents/` and `AgenticMath` as standards; use Deep Agents agents rather than plain LLM calls; keep LangChain-family code under `ai/`; reuse earlier chapters as reference; **test all the best code from the page** and deliver an organised study folder with new, never-tested code; do not read old commits.
4. Generalise it to all docs, and choose the groups by reading all pages, then synthesize each chapter.

Verdict: the direction is right and Planner and Synth stay as the two Pipeline processes. Two corrections: a chapter cannot literally come from one model call (small windows, flaky free providers), and "read all pages first" has to mean reading compact page cards, not raw pages.

## What the research says

| Question | Finding | Strength |
| --- | --- | --- |
| Outline before writing | STORM (Stanford) splits writing into pre-writing (research, then an outline) and writing (each section from the outline and its sources), and evaluates the outline as the quality gate. The same shape as "group first, then synthesize". | Primary paper ([arXiv 2402.14207](https://arxiv.org/abs/2402.14207), Feb 2024) |
| Grouping | Embedding clustering plus LLM naming and merging beat LLM-only iterative tree building on quality-per-cost ([Scychic, arXiv 2504.13834](https://arxiv.org/abs/2504.13834), Apr 2025, rev. Aug 2026). An LLM-built taxonomy can be structurally unstable without a clustering anchor ([SC-Taxo, 2026](https://www.emergentmind.com/papers/2605.00620)); iterative cluster-by-cluster labelling works ([arXiv 2510.15125](https://arxiv.org/pdf/2510.15125), Oct 2025). | Several papers; none compares cluster-first with per-document outlining |
| Number of chapters | **No evidence found.** Nothing in the search decides the optimal count. The default in this proposal (a reading-time budget) is a design choice. | Gap |
| Long context | Models grow less reliable as input grows, even on simple tasks (18 models tested, [Chroma Context Rot](https://research.trychroma.com/context-rot), 14 Jul 2025). Whether long context or retrieval wins depends on model, task and corpus; reports conflict. A map-reduce variant did best in a medical RAG study only ([arXiv 2412.15271](https://arxiv.org/html/2412.15271v1), Dec 2024). Practitioners converge on retrieve-then-reason. | Context Rot primary; the rest mixed |
| Code fidelity | No benchmark found for code fidelity across long context, map-reduce and retrieval. DD already avoids the problem: code blocks are substituted byte-exact by hash at render, so the model never retypes them. | Gap, mitigated by design |
| Running the code | Deep Agents treats sandboxes as backends that add an `execute` tool ([docs](https://docs.langchain.com/oss/python/deepagents/sandboxes.md)). No built-in self-verification: the generate, execute, judge, retry loop is hand-built in LangGraph ([Lightning example](https://lightning.ai/docs/platform/build/sandbox/examples/code-agent-langchain)). The `langchain-opensandbox` package pins `deepagents>=0.6.12,<0.7` ([PyPI](https://pypi.org/project/langchain-opensandbox/)); the server has 0.6.8. Hosted sandboxes (Modal, Daytona, Runloop) conflict with the free-tier rule. | Docs primary; loop pattern from tutorials |

Caveats: several long-context-versus-RAG numbers came from secondary blogs and are not used here; the 2025–2026 clustering papers each stand alone; nothing above was run on this project's corpora.

## Why the current design is heavier than the goal needs

| Observation | Where |
| --- | --- |
| About 3 model calls per page before any chapter exists: `page_judge` (off-topic), `page_distiller`, `page_scorer` (assignment ladder with up to four steps per page). | Planner |
| Chapter count comes from a target and the architect's judgment, not from how long the chapter takes to read. | Planner |
| A chapter passes through 7 nodes and up to 5 refine iterations (judge, reviewer, rewrite); a source is routed block by block to sections (`digest_construct`) before anyone writes. | Synth |
| 21 agent roles in all (8 planner, 13 synth). | Both |
| Code is validated (parses) but never run, and variants of the same code across pages are not reconciled: the writer sees routed blocks, not "the best version of this example". | Synth |

## Target architecture

The shell stays: the Celery tasks, the `Binding`/`drive()` runner, progress events, thread-id prefixes, MinIO prefixes, the checkpointer, per-item content-addressed caches and the web contract. What changes is the inside of each process. Run v2 beside v1 behind a pipeline version flag so the two can be compared on the same doc.

### Planner v2 — four stages

```
corpus_load ──► page_cards ──► chapter_design ──► order_chapters ──► plan_write
 (+dedupe,       (1 call/page)   (cluster proposals,   (prereq DAG)
  code ledger,                    architect, score gate,
  reading time)                   curator for oversize)
```

1. **`corpus_load` (deterministic).** Load the ingested pages; drop identical texts (exists); build the URL tree; hash every code block into a **corpus ledger** (block → pages that carry it); estimate **reading minutes** per page (prose words at about 230 wpm plus a per-line cost for code, tuned later). Stubs and changelog-tier pages are dropped here with no model call.
2. **`page_cards` (one structured call per distinct page, replacing `off_topic` + `doc_distill`).** A card: `keep` and why (off-topic, marketing, duplicate of another page), `kind` (concept, how-to, tutorial, reference), `topic` (at most eight words), `teaches` (up to three points), `code_role` (none, illustrative, runnable example, signature only), `prereqs` (up to three topics). Cached by content digest, so a re-plan of an unchanged corpus calls no model.
3. **`chapter_design` (replacing `section_analyst`, `chapter_assign` and the proposal half of `chapter_propose`).**
   - *Deterministic proposals:* cluster the cards' embeddings hierarchically and cut at several levels, combined with the URL tree; each candidate partition is scored by cohesion, separation, balance and total reading minutes.
   - *Count policy:* `N*` = unique teaching minutes ÷ the target chapter length (default 25 min), allowed range about `N*±1`, each chapter between a minimum and a maximum length. Reference and API pages are not taught: they go to the chapter's **appendix** (read on demand), so they do not inflate chapter length.
   - *Architect (existing agent, adapted):* sees the candidate partitions and the cards, not the pages (it can open a page on `/corpus/` when a card is unclear). It picks, merges or splits, and writes each chapter's title, **learning objective** and prerequisite topics. The existing `score_outline` gate stays and gains the new measures (minutes in budget, no near-duplicate chapters, prerequisites acyclic). The critic subagent stays, with one lens.
   - *Assignment:* page → chapter follows the architect's groups (URL prefixes and cluster ids). Only leftovers go to one batched call, or to the nearest centroid. No per-page ladder.
   - *Curator (existing):* splits a chapter over the maximum, using `cluster_units` and `validate_split`.
4. **`order_chapters`.** Topological order from the prerequisite graph; the existing sequencer samples and Borda vote only break ties.

`plan.json` keeps its shape and gains `reading_minutes`, `objective`, `prereqs` and `appendix` per chapter, so the study loop and the web read it unchanged.

### Synth v2 — one chapter run, five stages

```
prepare ──► outline ──► write (fan-out per step) ──► verify ──► judge ──(≤1 targeted revision)──► render
(ledger)    (steps +       (explanation + code by      (static,    (11 gates +          (byte-exact
             ledger refs)    hash; derive if needed)     then run)   5 criteria)           substitution + code/)
```

1. **`prepare` (deterministic).** Load the chapter's pages and build the chapter **code ledger**: exact duplicates collapse; near-variants (same imports and entry call, high token overlap) form a variant set; each entry records hash, language, lines, parse status and role (setup, usage, full example). A deterministic score picks a canonical variant (parses, has imports, within the length band, from the most specific page). Only ambiguous sets reach a model.
2. **`outline` (`chapter_outliner`, existing).** Input: objective, cards, headings and the ledger summary, not the blocks. Output: an ordered list of **steps**, each with its goal, the ledger hashes it uses and the pages it draws on; where the topic allows, one running example that grows step by step. `validate_outline` stays and gains the reading-minutes check.
3. **`write` (`section_writer`, existing, `Send` per step).** Reads its step's pages and blocks from `/sources/` and `/vault/`, writes the explanation and refers to code by hash. Where blocks must be combined or adapted, `code_deriver` writes a new snippet, flagged `derived`. Concurrency is capped under the Rotator's 16-concurrent-call limit, which other sessions share.
4. **`verify` (new).** Assemble the chapter's code in order into a project, then check in tiers: (a) parses; (b) imports and symbols resolve against the pinned package version; (c) runs, if self-contained, with a timeout, no secrets and no network. Each block ends **verified, unverified or failed** with its trace. A failure goes back to the writer for that step once; otherwise it is shown as unverified in the chapter, never passed silently. Tier (c) needs a sandbox backend (below) and is enabled per catalog entry.
5. **`judge` (`chapter_judge`, existing).** The 11 deterministic gates stay, with new ones: reading minutes in budget, every must-teach ledger entry used or dropped on purpose, no repeated block, verify counts. One LLM judge call. One targeted revision by `chapter_reviewer` (rewrite only the named steps), not five iterations.
6. **`render` (existing).** Byte-exact substitution by hash. New output: a `code/` folder next to `chapter.md`, the "organised study folder" of the user's Codex workflow, downloadable.

`book_harmonize` and the drift check stay as the cross-chapter pass.

### Removed or merged

| v1 | v2 |
| --- | --- |
| `off_topic` + `doc_distill` | `page_cards` |
| `section_analyst`, `chapter_assign` (+ `page_scorer`) | `chapter_design` (clusters + architect + group assignment) |
| `digest_construct` (`source_router` per chunk) | the ledger and the outline's hash references |
| `sawc_derive` | `code_deriver` called by the writer |
| `mgsr_replan` loop (up to 5 iterations) | at most one targeted revision |

Roles drop from 21 to about 11 (`page_card`, `chapter_architect`, `plan_critic`, `chapter_curator`, `chapter_sequencer`, `chapter_outliner`, `section_writer`, `code_deriver`, `chapter_judge`, `chapter_reviewer`, plus the harmonize roles). Not measured: the call and token savings.

### Sandbox for tier (c) verification

- Constraints: free tier only, no inference inside COELHO Cloud (code execution is not inference, but the same caution applies), generated code is untrusted.
- Shape: a custom Deep Agents backend whose `execute` runs in a throwaway pod (or container) with no egress, CPU and memory limits and a wall-clock cap. Dependencies come from a prebuilt runtime image per catalog entry (`verify: {pip: [...]}`), not from the network at run time.
- Start with tiers (a) and (b) in the worker; add (c) for the LangChain-family docs first, where the user has working reference code to compare with.
- Most docs code needs keys or a network: expect "unverified" to be common and to be shown honestly.

### The chapter-writer prompt, generalised from the Codex prompt

Fixed in the writer's system prompt:
- Role: write one chapter that teaches how to use `<framework>` for `<objective>` in at most `<minutes>` reading minutes.
- Inputs: the chapter's pages on `/sources/`, the ledger on `/vault/`, the outline.
- Output: `chapter.md` and `code/`; steps in dependency order; each step states what to build and why before showing code.
- Rules: use only code from the ledger or derive it with `code_deriver`; never retype a block; no repeated code; no content that does not serve the objective; every block marked verified or unverified.
- A per-doc **code-standards skill**, replacing "analyze `deepagents/` and `AgenticMath`". For the LangChain family it is built from the user's studies; for any other doc it is built from the docs themselves (their own idioms, file layout, naming).

Left out on purpose: "do not read old commits" (the agent has no git), and "keep LangChain code under `ai/`" (a rule of the user's repo, not of the chapters).

## Migration

0. **Baseline.** Redeploy, re-ingest DeepAgents (see `DD-AGENTIC-DEEPAGENTS-ASSESSMENT-2026-11-07.md`), run the current Planner and Synth on it. Record chapters, reading minutes, calls, tokens, judge results.
1. **Planner v2** behind the flag; Synth v1 unchanged. Compare chapter count, balance and minutes with step 0.
2. **Synth v2:** ledger, outline with hash references, writer fan-out, single revision; remove `digest_construct`.
3. **`verify`:** static tiers first, then the sandbox for one framework.
4. **Export** the `code/` folder; add reading minutes and verified counts to the web.

## How to evaluate it

- A replay set per phase, like BD's: DeepAgents (72 pages, Tier 3) and one large Tier 1 doc, with the v1 outputs kept as the comparison.
- Measures: chapter count, minutes per chapter and their spread, share of ledger entries used, repeated blocks, verified/unverified/failed share, model calls and tokens, judge pass.
- Quality reference: the user's hand-built LangGraph chapters (`langgraph/00N_*`) as gold; pairwise-judge a generated chapter on the same page against the gold one for objectivity, step order and code usage. This also tunes the reading-time estimate and the target length.

## Risks and open questions

- The reading-minutes estimate is a heuristic; it must be tuned against the gold chapters.
- A running example that grows across steps does not exist for every doc (concept and reference docs); the outline must be allowed to produce independent steps.
- Tool-calling reliability of the free pool for the architect and the outliner is unproven (same open point as the current agents).
- Stable chapter identities across re-plans (Deep Agents `MemoryMiddleware` + `StoreBackend`, noted in the planner doc) matter more once chapter synthesis is expensive.
- The `ai/dd` agents, prompts and skills are the user's to write or review; the deterministic parts (ledger, clustering, reading minutes, the sandbox backend, the score measures) can be built on request.

## Addendum (same day): quality first, capstone chapters, a capability layer, and the order to ship

Two decisions from the user that change the priorities above. Where this section and the earlier ones disagree, this one wins.

### 1. Quality first, then objectivity

- **First, the best quality study.** A chapter is a **complete tutorial**: it teaches *all* the best code related to its topic, and it ends with a **capstone**, the best practical application that **combines all of those codes**. The reading-time budget is a soft limit that applies only after quality is secured; it never removes a best code from the chapter.
- **Then, as objective as possible.** Objectivity means no filler, no repeated explanation, no code that the capstone does not need, and the shortest path to each code's use. It is judged by removing redundancy, not coverage.
- Consequences for the design:
  - The unit of coverage is the **must-teach set** of the chapter's code ledger (the best canonical variants). The judge's first gate is that every must-teach entry is taught in a step and used in the capstone, or is dropped with a stated reason.
  - A chapter's shape is fixed: **idea → one step per best code (smallest runnable example, then what it adds) → capstone → what to try next.**
  - The capstone is the strongest quality proof: it must **run**. So verification moves from the end of the plan to the center. A chapter whose capstone does not run is not shipped as passed; it is shown as unverified, with the trace.
  - Planner's "good chapter" test gains a **capstone-ability** check: a group of pages is a chapter only if its best codes can compose into one application. A group that cannot is split, or its pages join the nearest chapter or the appendix.
  - Quality is measured before it is optimised: the user's hand-built LangGraph chapters are the gold set and a rubric (complete coverage of best codes, a capstone that runs, step order, objectivity) scores every generated chapter.

### 2. The capability layer for the agents

Agents get better by what they can read and call, not only by their prompts. Four parts, built before the new Synth agents so they have something to use:

| Part | What | Notes |
| --- | --- | --- |
| Skills (Deep Agents, progressive disclosure, one set per role) | `tutorial-structure` (the fixed shape above), `capstone-design` (pick the application that composes every must-teach code; acceptance = it runs), `best-code-selection` (how to choose among variants: complete, current API, idiomatic, smallest), `code-standards` (per doc, derived from the docs and, for the LangChain family, from the user's studies), `chapter-review` (the rubric, quality first, then objectivity), `verification-triage` (what to do with each failure kind) | Skill bodies kept short; the rubric uses examples taken from the gold chapters |
| Prompts | `SYSTEM.md` per role: planner cards, architect, outliner, writer, capstone designer, judge, reviewer. The writer's prompt is the generalised Codex prompt in the section above. | Few-shot from the gold chapters for the judge and the capstone designer |
| In-process tools (pure functions, deterministic) | ledger queries (`list_variants`, `get_block`, `compare_variants`), `search_corpus`, `reading_minutes`, and the existing `validate_outline`, `check_section`, `check_code`, `score_outline`, `validate_split` | The decision recorded in `BD-INGESTION-AGENTIC-AI-SOTA-2026-11-06.md` applies: pure in-process functions do not need MCP |
| MCP servers, only where a trust or service boundary exists | (a) **code execution**: a sandbox exposed as its own server, so generated code never runs in the worker's process; (b) **official docs lookup**: ask a framework's own docs or a docs-index server whether an API still exists in the current version (the LangChain docs footer advertises an MCP server for its docs; the workspace also has a Context7 server); (c) **package introspection**: resolve a symbol and its signature in the pinned version | Not researched in this session: before choosing servers, run one short sota-search on current docs-lookup and code-execution MCP servers, and on how Deep Agents consumes MCP tools. The project already runs a FastMCP server (`/mcp`), which could host (a) and (c). |

### 3. Order to ship (supersedes "Migration" above)

Each step ships alone, is behind the pipeline version flag, and has a stated finish line.

| # | Ship | Why here | Done when |
| --- | --- | --- | --- |
| 0 | **Measure the present.** Redeploy; re-ingest DeepAgents; run the current Planner and Synth live on it once | They have never run live; v2 needs a baseline, and live runs reveal what offline tests cannot | A run of each is assessed in `docs/`: chapters, minutes, calls, tokens, judge results, failures |
| 1 | **Quality yardstick.** The gold set (the user's LangGraph chapters), the rubric, and a pairwise judge that scores a generated chapter against its gold one | Quality is the first goal, and nothing else can be tuned without a measure | The step-0 chapters are scored; the score separates a known-good chapter from a known-weak one |
| 2 | **Capability layer, deterministic half.** The code ledger (dedupe, variant sets, canonical choice), the in-process tools, the reading-minutes estimate | Everything after reads the ledger | Ledger of DeepAgents' pages reviewed by hand: duplicates collapsed, canonical picks sensible |
| 3 | **Capability layer, agent half.** The skills and prompts above, the MCP choice (after its short sota-search), the sandbox backend with static verification tiers | The Synth v2 agents need them | Each skill and prompt exercised by a scripted-model test; the sandbox refuses network and respects limits |
| 4 | **Synth v2.** `prepare` (ledger), tutorial outline, per-step writers, capstone designer and writer, `verify`, judge with the rubric gates, one targeted revision. Fed by the **current** plan | Quality lives in Synth; the plan can stay as it is while chapters improve | On the DeepAgents chapters, the step-1 score beats the step-0 chapters, and every capstone is verified or honestly marked unverified |
| 5 | **Execution tier.** Run capstones and examples in the sandbox, per catalog entry (LangChain family first) | Moves the capstone from "parses" to "runs" | The capstones of the LangChain-family chapters run; failures return to the writer once |
| 6 | **Planner v2.** Page cards, candidate partitions, architect with capstone-ability and prerequisite checks, appendix, count policy | Only now does the count and grouping get optimised, with a quality measure to protect | The same docs get fewer or equal chapters than step 0 with equal or better step-1 scores |
| 7 | **Objectivity pass.** Reading-time budget as a soft limit, redundancy gates, merge/split by the measures | Objectivity is the second goal, after quality | Reading minutes fall with no drop in the rubric's coverage and capstone scores |
| 8 | **Export and UI.** The `code/` folder, verified/unverified badges, the capstone's run output, reading minutes | The user's "organised study folder" | A chapter downloads as `chapter.md` plus a folder that runs |
| 9 | **Scale out.** Tier 2–4 docs, per-doc code-standards skills, retire v1 | Only after the pipeline wins on the gold-comparable docs | Two docs outside the LangChain family pass the rubric |

Risks specific to this addendum: a capstone that composes every best code may be forced for docs whose codes do not combine (reference docs), so the Planner must be allowed to mark such a chapter "reference" with no capstone; running arbitrary docs code needs a prebuilt dependency image per framework; and the gold set exists only for LangChain-family docs, so quality for other docs rests on the rubric alone until more hand-built chapters exist.
