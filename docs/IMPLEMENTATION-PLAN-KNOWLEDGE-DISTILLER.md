# Knowledge Distiller — Implementation Plan (step-by-step)

> Companion to [`KNOWLEDGE-DISTILLER-ARCHITECTURE.md`](./KNOWLEDGE-DISTILLER-ARCHITECTURE.md). Executes the 11 architecture phases as 12 PR-sized steps, each with a learning goal and verification. Each step is approved separately.

## Learning-first framing

The user is learning by building. Every step is designed to teach one concept cleanly:

- **Layered build**: infrastructure (schemas) → pure logic (nodes) → wiring (graph) → serving (API). Each layer works on its own before the next is added.
- **Minimum-viable-first**: ship tier-1 ingestion + planner + synthesizer first. Grader, critic, episodic memory, exports bolt on afterward.
- **Explain decisions, not just code**: every step documents what pattern was used and why the alternatives were rejected.
- **Use live docs**: before each step, Context7 or SearXNG is queried for the current API surface of the library involved — no version-drift bugs.

## Step map

| # | Step | Files touched | LOC | Learning goal |
|---|---|---|---|---|
| 1 | Schemas & state types | `schemas/knowledge.py`, `schemas/state.py` | ~120 | Pydantic + TypedDict + `Annotated[..., operator.add]` reducer |
| 2 | Prompts | `schemas/prompts_knowledge.py` | ~150 | Prompt engineering for structured output; tone-adapter pattern |
| 3 | Tiered ingestion service | `services/ingestion_docs.py` | ~200 | Graceful fallback waterfall; async HTTP; Crawl4AI v0.8 |
| 4 | Planner node | `agents/knowledge.py` (part) | ~60 | `with_structured_output()` for deterministic LLM decisions |
| 5 | Synthesizer + grader + Self-Refine loop | `agents/knowledge.py` (part) | ~180 | Self-Refine iteration pattern; per-chapter artifact writing |
| 6 | Critic node | `agents/knowledge.py` (part) | ~80 | RAGAS-style citation verification |
| 7 | Assembler node | `agents/knowledge.py` (part) | ~60 | Cross-chapter summary + debt tracking |
| 8 | Graph builder | `graphs/knowledge.py` | ~120 | LangGraph `StateGraph` + dynamic `Send()` fan-out |
| 9 | Celery task | `tasks/knowledge.py` | ~80 | Background long-running job with progress + checkpointing |
| 10 | FastAPI router | `routers/v1/knowledge.py`, `app.py` | ~200 | REST + SSE streaming on top of a LangGraph job |
| 11 | Export service | `services/pandoc.py`, `services/anki.py`, export task + endpoint | ~140 | Markdown → PDF/HTML/EPUB/Anki via Pandoc + genanki |
| 12 | End-to-end smoke test | `notebooks/test_knowledge_distiller.ipynb` or a CLI | ~60 | Running the whole pipeline against a real framework |

Total: ~1,450 LOC across ~12 files. Comparable in size to `graphs/adaptive.py` + `graphs/youtube.py` combined.

---

## Step 1 — Schemas & state types

**Scope:** Define every typed structure the pipeline touches, in one place, before writing any logic.

**Files:**
- `apps/fastapi/schemas/knowledge.py` (new): `UserProfile`, `ChapterPlan`, `ChapterPlanList`, `ChapterResult`, `GraderEvaluation`, `CriticAssessment`
- `apps/fastapi/schemas/state.py` (edit): add `KnowledgeDistillerState`

**Patterns used:**
- `pydantic.BaseModel` with `Field(...)` descriptions — LLM `with_structured_output()` reads these to understand what to produce.
- `TypedDict` for LangGraph state (not BaseModel — LangGraph's reducer system requires `Annotated` on dict keys, which Pydantic doesn't play well with).
- `Annotated[list[dict], operator.add]` for fields that parallel `Send()` workers write into.

**What you'll learn:** The split between "Pydantic for LLM I/O" and "TypedDict for graph state." This is the single most common LangGraph newcomer confusion — docs say "use Pydantic," tutorials use TypedDict. We use both, intentionally, for different layers.

**Verification:** `python -c "from schemas.knowledge import UserProfile; print(UserProfile.model_json_schema())"` renders cleanly.

**Dependencies:** None.

---

## Step 2 — Prompts

**Scope:** Write the system+user prompt templates for every LLM call in the pipeline. One file, no logic, just text.

**Files:**
- `apps/fastapi/schemas/prompts_knowledge.py` (new): `PLANNER_PROMPT`, `SYNTHESIZER_PROMPT`, `GRADER_PROMPT`, `ADJUSTMENT_PROMPT`, `CRITIC_PROMPT`, `ASSEMBLER_PROMPT`
- Tone-adapter helper: `build_tone_block(user_profile) -> str`

**Patterns used:**
- `ChatPromptTemplate.from_messages([...])` — LangChain's canonical prompt constructor, same pattern used in `schemas/prompts.py`.
- Tone adapter injected as an f-string block inside the synthesizer prompt, not as a separate prompt — keeps coverage guarantees constant while varying only presentation.

**What you'll learn:** How to write prompts that drive `with_structured_output()` reliably — critical field names match the Pydantic schema, few-shot examples sit inside the prompt, and the output constraint is expressed as a natural-language rule the LLM will actually follow.

**Verification:** Load the file, render a prompt with dummy values, eyeball it.

**Dependencies:** Step 1 (references schema names in prompt text).

---

## Step 3 — Tiered ingestion service

**Scope:** The waterfall that extracts any framework's docs into `research/raw/*.md`.

**Files:**
- `apps/fastapi/services/ingestion_docs.py` (new)

**Tiers (executed in order, stop at first success):**

| Tier | Method | What it probes | Tool |
|---|---|---|---|
| 1 | `/llms-full.txt` | whole concatenated docs in one file | `httpx.AsyncClient` |
| 2 | `/llms.txt` | index listing links to `.md` endpoints | `httpx.AsyncClient` |
| 3 | `/sitemap.xml` | standard web sitemap | `httpx.AsyncClient` + XML parse |
| 4 | Crawl4AI v0.8 BFS | sidebar deep-crawl with scorers | `crawl4ai` |

Each tier writes the same output shape: files under `<study_root>/research/raw/*.md` + a manifest entry. Downstream nodes don't care which tier ran.

**Patterns used:**
- `async def` everywhere — uses `httpx.AsyncClient` context manager.
- `asyncio.Semaphore(5)` on tier 4 to respect rate limits.
- Graceful probing: 404 / empty / `<html>` content means "move to next tier," not "fail."

**What you'll learn:**
- Why the `llms-full.txt` standard exists and how broadly it's adopted (1,263+ sites).
- How to build a resilient waterfall (any tier can be skipped without breaking later tiers).
- Crawl4AI v0.8's actual current API — fetched via Context7 / SearXNG at implementation time.

**Verification:** Three probe tests: against `https://docs.pixeltable.com/` (has llms-full.txt), `https://docs.langchain.com/` (probably tier 2 or 3), `https://docs.crawl4ai.com/` (tier 4 fallback).

**Dependencies:** Step 1 (writes manifest entries).

---

## Step 4 — Planner node

**Scope:** Given the raw files, decide N ∈ [4, 12] chapters and assign files to each.

**Files:**
- `apps/fastapi/agents/knowledge.py` (new — add `planner_node` function)

**Patterns used:**
- `llm.with_structured_output(ChapterPlanList, method="function_calling")` — deterministic, no regex parsing.
- Read all file names + first ~500 chars of each into the planner's context — planner doesn't need full content, just headings/structure to cluster.
- Bounded N via validation inside `ChapterPlanList` Pydantic validator.

**What you'll learn:** `with_structured_output()` — the single most useful LangChain feature for production. Why it beats JSON-mode or regex parsing: it guarantees schema conformance and surfaces errors as Python exceptions.

**Verification:** Feed a small fake corpus (5 markdown files), assert planner returns `ChapterPlanList` with 4 ≤ N ≤ 12 and every file assigned to exactly one chapter.

**Dependencies:** Steps 1, 2, 3.

---

## Step 5 — Synthesizer + grader + Self-Refine loop

**Scope:** The heart of the system. Generate a chapter, evaluate it across 8 dimensions, adjust if below threshold, retry up to 2×.

**Files:**
- `apps/fastapi/agents/knowledge.py` (edit — add `synthesize_chapter`)

**Patterns used:**
- Self-Refine loop: `for iteration in range(3)` — synthesize → grade → (accept | adjust | continue).
- Trajectory tracking: every attempt preserved for episodic memory (step 7).
- Adjustment-prompt generator: converts low-dimension scores into specific, actionable instructions (not "improve quality").
- Write three files per chapter at accept time: `README.md`, `challenges.md`, `flashcards.json`.

**What you'll learn:** Self-Refine / Reflexion pattern implementation. Madaan et al.'s paper pitches this abstractly; here's what it actually looks like in production code. Also: why bounded iterations matter (infinite loops on poor taste).

**Verification:** Stub-run a single chapter against a tiny corpus, inspect the iteration log to see grader scores evolving.

**Dependencies:** Steps 1, 2, 4.

---

## Step 6 — Critic node

**Scope:** After all chapters synthesize, a cheap verifier checks citations + detects hallucinations.

**Files:**
- `apps/fastapi/agents/knowledge.py` (edit — add `critic_node`)

**Patterns used:**
- Regex-extract `# docs: <ref>` comments from synthesized chapters.
- For each citation, verify the referenced `research/raw/*.md` file exists and contains the quoted fragment (or close paraphrase, verified by a cheap judge LLM).
- Emit `validation_report.json` with per-chapter scores.

**What you'll learn:** RAGAS's faithfulness metric, in ~80 lines. Why citation-over-explanation scales better than generation-time verification.

**Verification:** Seed a fake chapter with a broken citation (`# docs: nonexistent.md`), assert critic flags it.

**Dependencies:** Step 5.

---

## Step 7 — Assembler node

**Scope:** Final pass — write `summary.md` (index + reading plan + market roadmap), `DEBT.md` (unresolved issues), update episodic memory.

**Files:**
- `apps/fastapi/agents/knowledge.py` (edit — add `assembler_node`)

**Patterns used:**
- Summary generation: LLM reads all chapter `README.md` headings + `research/plan.json`, produces a single coherent index.
- Episodic memory update: EMA (exponential moving average) on observed preferences; logged to a per-user PostgreSQL row.

**What you'll learn:** Episodic memory is just persisted state with a simple update rule. No mysticism.

**Verification:** After a full pipeline run, confirm `summary.md` lists all chapters and `DEBT.md` contains critic's flagged items.

**Dependencies:** Steps 5, 6.

---

## Step 8 — Graph builder

**Scope:** Wire all nodes into a `StateGraph` with a dynamic `Send()` fan-out.

**Files:**
- `apps/fastapi/graphs/knowledge.py` (new — `KnowledgeDistillerGraph` class, mirroring `AdaptiveRAGGraph`)

**Patterns used:**
- Same class-based builder pattern as `graphs/adaptive.py`: closures bind LLM + services to nodes at build time.
- Conditional edge from planner using `Send()` list to fan out N chapters.
- `operator.add` reducer accumulates chapter results.

**What you'll learn:** The full LangGraph 1.1.x graph-building idiom. How `add_conditional_edges` with a `Send()`-returning function creates the dynamic fan-out. Why the entry node must return `{}` (not the state) before fanning out.

**Verification:** `graph = builder.build(...)` compiles without errors; invoking with a fake initial state hits every node in the expected order.

**Dependencies:** Steps 4–7.

---

## Step 9 — Celery task

**Scope:** Wrap the graph in a Celery task so FastAPI can return immediately while the job runs for minutes.

**Files:**
- `apps/fastapi/tasks/knowledge.py` (new)

**Patterns used:**
- Same shape as `tasks/ingestion.py`, `tasks/graph.py`: Celery task → builds the graph with `AsyncPostgresSaver` → runs `graph.ainvoke()`.
- Progress updates via `self.update_state(state="PROGRESS", meta={...})` — lets the `/tasks/{id}` endpoint expose live status.
- Task routed to the `llm` queue (same as `graph` ingestion).

**What you'll learn:** How to bridge async LangGraph into sync Celery. `asyncio.run()` inside a Celery task is the simplest pattern; the alternative is Celery's experimental async support, not production-stable yet.

**Verification:** `celery -A celery_app worker -Q llm` picks up a test job; `AsyncResult(task_id).state` progresses PENDING → STARTED → PROGRESS → SUCCESS.

**Dependencies:** Step 8.

---

## Step 10 — FastAPI router

**Scope:** The public API — 6 endpoints backing the whole feature.

**Files:**
- `apps/fastapi/routers/v1/knowledge.py` (new)
- `apps/fastapi/app.py` (edit — register the router)

**Endpoints:**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/knowledge/studies` | Create study, returns `{study_id, task_id}` |
| `GET` | `/api/v1/knowledge/studies/{study_id}` | Status + phase + progress |
| `GET` | `/api/v1/knowledge/studies/{study_id}/stream` | SSE, node-by-node updates |
| `GET` | `/api/v1/knowledge/studies/{study_id}/tree` | File manifest |
| `GET` | `/api/v1/knowledge/studies/{study_id}/chapters/{n}` | Return one chapter's 3 artifacts |
| `DELETE` | `/api/v1/knowledge/studies/{study_id}` | Cancel |

**Patterns used:**
- Mirrors `routers/v1/youtube/agents.py` for consistency.
- SSE endpoint uses `graph.astream(..., stream_mode="updates")` — same pattern as the existing Adaptive RAG `/search/stream`.

**What you'll learn:** SSE (Server-Sent Events) on top of LangGraph's `astream`. Why SSE beats WebSockets for unidirectional progress streaming (simpler, works through all proxies, native browser support).

**Verification:** `curl -N http://localhost:8000/api/v1/knowledge/studies/<id>/stream` prints JSON updates as the job progresses.

**Dependencies:** Step 9.

---

## Step 11 — Export service (Pandoc + Anki)

**Scope:** On-demand exports from the canonical markdown.

**Files:**
- `apps/fastapi/services/pandoc.py` (new)
- `apps/fastapi/services/anki.py` (new)
- `apps/fastapi/tasks/knowledge_export.py` (new)
- `apps/fastapi/routers/v1/knowledge.py` (edit — add `POST /{id}/export`)
- `apps/fastapi/Dockerfile.fastapi` (edit — install `pandoc`, `texlive-xetex`)

**Patterns used:**
- `pypandoc.convert_file()` with explicit PDF engine (`xelatex`) for code-heavy output.
- `genanki` for `flashcards.json` → `.apkg` — one deck per chapter, 1 model (Q/A).

**What you'll learn:** Pandoc pipelines — the 30-year-old universal converter is still the 2026 gold standard for this. Also: how to keep exports regeneratable (cache key = hash of source md + template version).

**Verification:** After an end-to-end run, `POST /<id>/export?format=pdf` produces a readable PDF in `<study_root>/exports/`.

**Dependencies:** Step 10.

---

## Step 12 — End-to-end smoke test

**Scope:** Run the whole pipeline against a small real framework and verify outputs.

**Files:**
- `notebooks/test_knowledge_distiller.ipynb` (new) OR
- `apps/fastapi/scripts/smoke_test_knowledge.py` (new)

**Target framework:** `pydantic` or `jinja2` — both have:
- Moderate corpus size (~50–100 pages)
- Public `/llms-full.txt` OR clean sitemap
- Clear conceptual boundaries (good planner test)

**Success criteria:**
1. Pipeline completes in < 15 minutes end-to-end
2. Every chapter scores ≥ 0.80 on the adaptive grader
3. Critic reports ≥ 95% citation validity
4. `summary.md` correctly indexes all chapters
5. `POST /export?format=pdf` produces a readable PDF
6. Generated flashcards import cleanly into Anki

**What you'll learn:** How to design a smoke test that catches the bugs that unit tests miss (prompt drift, model output flakiness, race conditions in Send()).

**Dependencies:** Steps 1–11.

---

## Pacing expectations

Rough estimates per step, **assuming one step per interactive session**:

| Step | Complexity | Interactive time |
|---|---|---|
| 1 | Low | 15 min |
| 2 | Low | 20 min |
| 3 | Medium-high (new concepts) | 45 min |
| 4 | Low | 20 min |
| 5 | High (biggest step) | 60 min |
| 6 | Medium | 30 min |
| 7 | Low | 20 min |
| 8 | Medium (wiring, easy to misstep) | 40 min |
| 9 | Low | 20 min |
| 10 | Medium | 40 min |
| 11 | Medium | 40 min |
| 12 | Variable | 30–90 min |

Total estimate: ~6–8 hours of focused work spread across 12 sessions.

---

## Live-docs protocol

Before writing any code for a step, I will:
1. Resolve the library ID via `mcp__plugin_context7_context7__resolve-library-id` for the primary library in play (e.g., `deepagents`, `langgraph`, `crawl4ai`).
2. Query current docs via `mcp__plugin_context7_context7__query-docs` for the exact API surface being used.
3. If Context7 lacks coverage (e.g., brand-new releases), fall back to `mcp__searxng__searxng_web_search` + `mcp__searxng__web_url_read` against official docs.
4. Only then write code.

This prevents version-drift bugs — the user's prior experience with libraries changing APIs between training cutoffs makes this non-negotiable.

---

*Created: 2026-04-19*
*Canonical architecture: [`KNOWLEDGE-DISTILLER-ARCHITECTURE.md`](./KNOWLEDGE-DISTILLER-ARCHITECTURE.md)*
