# Knowledge Distiller — Validated Architecture (2026-04-19)

> Single source of truth. Supersedes `STUDY-GENERATOR-ARCHITECTURE.md`, `STUDY-GENERATOR-ARCHITECTURE-REFERENCE.md`, `STUDY-GENERATOR-ARCHITECTURE-FINAL.md` where they disagree. Companion docs still valid: `KNOWLEDGE-DISTILLER-WHOLE-DOCS-VARIABLE-TONE.md` (principle), `STUDY-GENERATOR-ADAPTIVE-GRADER.md` (grader detail), `INTEGRATION-PATTERN-DeepAgents-LangGraph.md` (DeepAgents + LangGraph integration).

## Purpose

User supplies a framework + profile. System extracts the **entire** official documentation, synthesizes complete study material from the **whole corpus** (coverage never filtered by level — only presentation tone varies), and delivers the result as markdown with optional PDF/Anki exports.

Goal: compress 300–500 page official docs into ~80–120 pages of production-focused, hiring-grade study material the user actually reads.

---

## Validation summary (April 19, 2026)

Each architectural choice in prior design docs was re-validated against current SOTA. Results:

| Pillar | Prior design | Status | Source |
|---|---|---|---|
| Agent harness | DeepAgents 0.5.x + LangGraph 1.1.x | ✅ Confirmed SOTA | LangChain `DeepAgents Deploy` (Apr 9, 2026) |
| Parallel fan-out | LangGraph `Send()` + `operator.add` | ✅ Confirmed SOTA | LangGraph Orchestrator-Worker pattern; supports dynamic N |
| Self-Refine loop | 2 iterations, threshold 0.85 | ✅ Confirmed SOTA | Madaan et al. follow-ups; LangGraph 2026 guides |
| LLM-as-Judge | Multi-dimensional grader | ✅ Confirmed SOTA | zylos.ai (Apr 10, 2026): 50%+ teams runtime judge |
| Whole-docs + variable-tone | Presentation adapts, coverage constant | ✅ Confirmed SOTA | Nature (Mar 2026) "grade-specific teachers" |
| Checkpointer | `AsyncPostgresSaver` | ✅ Confirmed SOTA | LangGraph continues to recommend |
| Docs ingestion | Crawl4AI sidebar crawl | ⚠️ Improvable | See Delta #1 |
| Chapter count | Fixed at 8 | ⚠️ Improvable | See Delta #2 |
| Pedagogy | Chapter text only | ⚠️ Improvable | See Delta #3 |
| Synthesis model | GLM-5.1 | ❌ Deprecated 2026-04-20 | See Delta #4 |
| Output format | `.md` primary | ✅ Confirmed SOTA | Pandoc v3.9, 2026 docs-as-code consensus |

## Deltas from prior docs

### Delta 1 — Tiered ingestion (was: only Crawl4AI)

Prior docs jumped straight to Crawl4AI sidebar crawling. A 2024 standard proposed by jeremyhoward, now actively adopted by 1,263+ sites (Stripe, Anthropic, Mintlify-hosted docs, Pixeltable, etc.), publishes the entire documentation as a single concatenated markdown file at `/llms-full.txt`. When available, it replaces ~300 HTTP calls with one.

**New ingestion waterfall:**

1. **Tier 1** — try `<docs-root>/llms-full.txt`. One HTTP call via `web_url_read`. If present and non-empty, skip tiers 2–3 entirely.
2. **Tier 2** — Context7 MCP (`resolve-library-id` → `query-docs`). Version-aware, code-example-heavy, rate-limited (1000 req/month free in 2026). Good when llms-full.txt absent but library is on Context7.
3. **Tier 3** — Crawl4AI v0.8.x `BFSDeepCrawlStrategy` with `URLPatternFilter` + `KeywordRelevanceScorer` + `max_pages=500`. The original plan. Use `prefetch=True` first for fast URL discovery, then full fetch.

All tiers write the result into `research/raw/*.md` with the same downstream shape, so planner/synthesizer don't care which tier supplied the content.

### Delta 2 — Dynamic chapter count (was: fixed 8)

Framework complexity varies: Tailwind docs ≈ 4 chapters of natural material, CUDA deep-dive ≈ 12. Fixed 8 pads or truncates.

**New rule:** planner decides N (bounded 4–12) based on semantic grouping of the raw corpus, then `Send()` fans out N workers. `operator.add` aggregates regardless of N. This is the textbook "orchestrator-worker" pattern LangGraph was designed for.

### Delta 3 — Pedagogy artifacts (was: chapter text only)

Cognitive science (Nature Mar 2026; ASEE; PMC educational studies) is unambiguous: passive reading < active recall + spaced repetition. Generating just chapter text leaves ~40% of retention on the table.

**New per-chapter outputs (generated in the same synthesis call — ~0 marginal cost):**

| File | Purpose |
|---|---|
| `chapterNN/README.md` | Main content (existing) |
| `chapterNN/challenges.md` | 5–10 active-recall questions / mini-exercises (new) |
| `chapterNN/flashcards.json` | Anki-importable `{front, back}` pairs (new) |

### Delta 4 — Model swap (was: GLM-5.1 synthesis)

Per `NVIDIA-NIM-FREE-MODELS-OPENCLAUDE.md`, GLM-5 is **deprecated 2026-04-20 on NIM** and GLM-5.1 has no migration path on the free tier yet. Prior arch documents prescribe GLM-5.1 for synthesis — that endpoint dies tomorrow.

**New model routing (NIM free tier):**

| Role | Model | Rationale |
|---|---|---|
| Synthesis (code-heavy, per-chapter) | `minimaxai/minimax-m2.7` | 56% SWE-Pro, 57% Terminal-Bench, most JSON-stable on NIM (user's research) |
| Planner (chapter structure) | `moonshotai/kimi-k2-thinking` | Best reasoning / decomposition; single-shot only (no compaction hangs) |
| Grader (8-dim judge) | `moonshotai/kimi-k2-thinking` | Nuanced evaluation |
| Critic (citation verify, cheap) | `nvidia/nemotron-3-nano-30b-a3b` | 1M context, 2–3× faster than Qwen3-30B |
| Fallback | `qwen/qwen3.5-397b-a17b` (+ `enable_thinking=false`) | Strong tool-calling backup |

Application's existing Groq-first fallback chain in `apps/fastapi/app.py` is kept as-is — Groq handles speed-sensitive calls (classify, direct answers); NIM handles heavy synthesis.

### Delta 5 — Output formats

`.md` is the canonical, editable source of truth. PDF and Anki are derivative, regeneratable artifacts produced by Pandoc and a flashcard exporter on demand.

| Format | Role | How produced |
|---|---|---|
| `.md` | Canonical | Synthesizer writes directly |
| `.pdf` | User export (tablet/print) | `pandoc --pdf-engine=xelatex` in a Celery task |
| `.html` | User export (web) | `pandoc -t html5 --standalone` |
| `.apkg` (Anki) | User export (SRS) | `genanki` from `flashcards.json` |
| `.epub` | User export (e-reader) | `pandoc -t epub3` |

No `.docx`, no `.mdx`, no AsciiDoc. Markdown + Pandoc handles 100% of plausible needs.

---

## Final pipeline

```
┌────────────────────────────────────────────────────────────────┐
│ USER REQUEST                                                    │
│  Input: framework, version?, user_profile                      │
│         { level, target_markets, mastered_tech,                │
│           portfolio_refs, acceptance_threshold=0.85 }          │
└────────────────────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────────┐
│ STEP 1  INGEST (tiered)                                         │
│  1. try /llms-full.txt                → writes research/raw/   │
│  2. else Context7 MCP query-docs      → writes research/raw/   │
│  3. else Crawl4AI BFS + scorers       → writes research/raw/   │
│  Output: research/manifest.md (sources + tier used)            │
└────────────────────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────────┐
│ STEP 2  PLAN  (kimi-k2-thinking, structured output)             │
│  - Decide chapter count N ∈ [4, 12]                            │
│  - Assign research/raw/*.md to chapters by semantic grouping   │
│  - Output: research/plan.json { chapters: [{num, title,        │
│    files, goal}] }                                             │
└────────────────────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────────┐
│ STEP 3  SYNTHESIZE + GRADE  (N parallel Send() workers)         │
│                                                                 │
│   ┌──────────────┐   ┌───────────┐   ┌──────────────┐         │
│   │ SYNTHESIZER  │──▶│  GRADER   │──▶│  ADJUSTMENT  │         │
│   │ minimax-m2.7 │   │ kimi-k2-t │   │  GENERATOR   │         │
│   │ writes md +  │   │ 8-dim     │   │              │         │
│   │ challenges + │   │ eval      │   │              │         │
│   │ flashcards   │   │           │   │              │         │
│   └──────────────┘   └───────────┘   └──────┬───────┘         │
│         ▲                                    │                 │
│         │        score < 0.85?               │                 │
│         └────────────────────────────────────┘                 │
│                                                                 │
│   • N concurrent via LangGraph Send()                          │
│   • Max 2 refinement iterations (Self-Refine)                  │
│   • Tone vars in prompt; coverage held constant                │
│   • Each worker writes:                                        │
│     chapterNN/README.md                                        │
│     chapterNN/challenges.md                                    │
│     chapterNN/flashcards.json                                  │
│   • Reducer: operator.add on synthesis_results                 │
└────────────────────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────────┐
│ STEP 4  CRITIC  (nemotron-3-nano, RAGAS-style)                  │
│  - Faithfulness: each claim traceable to research/raw/?        │
│  - Citation integrity: every `# docs:` reference resolves?     │
│  - Code compile: syntax-valid per language detected?           │
│  - Output: validation_report.json                              │
└────────────────────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────────┐
│ STEP 5  ASSEMBLE  (minimax-m2.7)                                │
│  - summary.md: index + reading order + market roadmap          │
│  - DEBT.md: unresolved grader/critic issues                    │
│  - Episodic memory update (preferences learned from trajectory)│
└────────────────────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────────┐
│ OPTIONAL EXPORTS  (user-triggered, on-demand)                   │
│  POST /knowledge/{id}/export?format=pdf|html|epub|anki         │
│  Celery task: pandoc / genanki                                 │
└────────────────────────────────────────────────────────────────┘
```

---

## Output tree

```
studies/<framework>-<version>/
├── summary.md                       # index + reading plan + money roadmap
├── DEBT.md                          # unresolved issues from grader/critic
├── research/
│   ├── manifest.md                  # source URLs + tier used
│   ├── raw/                         # ingested docs, one file per page/section
│   ├── plan.json                    # planner chapter assignments
│   └── synth/                       # raw synthesis drafts (pre-assembly)
├── chapter01/
│   ├── README.md                    # canonical chapter content
│   ├── challenges.md                # active-recall questions
│   └── flashcards.json              # Anki-importable Q/A
├── chapter02/ ... chapterNN/
└── exports/                         # on-demand, regeneratable
    ├── <framework>.pdf
    ├── <framework>.epub
    ├── <framework>.anki.apkg
    └── <framework>.html
```

---

## State schema

```python
# apps/fastapi/schemas/state.py (extended)

from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field
import operator

class UserProfile(BaseModel):
    level: Literal["junior", "mid", "senior"] = "senior"
    target_markets: list[str] = Field(default_factory=list)       # e.g. ["uae", "singapore"]
    mastered_technologies: list[str] = Field(default_factory=list)
    portfolio_refs: list[str] = Field(default_factory=list)        # e.g. ["COELHO RealTime"]
    acceptance_threshold: float = 0.85

class ChapterPlan(BaseModel):
    number: int
    title: str
    goal: str                                                      # one-sentence outcome
    assigned_files: list[str]

class ChapterResult(BaseModel):
    number: int
    score: float
    iterations: int
    content_path: str                                              # chapterNN/README.md
    challenges_path: str                                           # chapterNN/challenges.md
    flashcards_path: str                                           # chapterNN/flashcards.json

class KnowledgeDistillerState(TypedDict):
    # Input
    framework: str
    version: Optional[str]
    docs_url: Optional[str]
    user_profile: UserProfile
    study_root: str                                                # studies/<framework>-<version>/

    # Phase status
    current_phase: Literal["ingest", "plan", "synth", "critic", "assemble", "complete", "failed"]
    ingest_tier_used: Literal["llms-full-txt", "context7", "crawl4ai", "none"]

    # Ingest outputs
    raw_files: list[str]
    manifest: list[dict]

    # Plan outputs
    plan: list[ChapterPlan]

    # Synth outputs (parallel Send() reducer)
    synthesis_results: Annotated[list[dict], operator.add]

    # Critic outputs
    validation_report: Optional[dict]

    # Assemble outputs
    summary_path: Optional[str]
    debt_path: Optional[str]
```

---

## FastAPI surface

```
POST   /api/v1/knowledge/studies
       body: { framework, version?, docs_url?, user_profile }
       → { study_id, task_id, status: "queued" }

GET    /api/v1/knowledge/studies/{study_id}
       → { status, phase, chapters_complete, score_avg, ... }

GET    /api/v1/knowledge/studies/{study_id}/stream
       → SSE: node-by-node updates (ingest → plan → synth per chapter → ...)

GET    /api/v1/knowledge/studies/{study_id}/tree
       → full file manifest of the study directory

GET    /api/v1/knowledge/studies/{study_id}/chapters/{n}
       → chapter README, challenges, flashcards

POST   /api/v1/knowledge/studies/{study_id}/export
       body: { format: "pdf" | "html" | "epub" | "anki" }
       → { task_id } (Celery)

DELETE /api/v1/knowledge/studies/{study_id}
       → cancel if running, keep artifacts
```

Backed by a Celery task `tasks.knowledge.run_distiller` that runs the LangGraph workflow with `AsyncPostgresSaver` checkpointing, mirroring the existing Celery patterns in `apps/fastapi/tasks/*.py`.

---

## File layout inside `apps/fastapi/`

```
apps/fastapi/
├── agents/
│   └── knowledge.py                 # DeepAgents subagent specs (synth, grader)
├── graphs/
│   └── knowledge.py                 # StateGraph builder + node wiring
├── services/
│   ├── ingestion_docs.py            # NEW: tiered ingest (llms-full / Context7 / Crawl4AI)
│   ├── pandoc.py                    # NEW: export pipeline
│   └── anki.py                      # NEW: flashcards.json → .apkg
├── schemas/
│   ├── state.py                     # extended with KnowledgeDistillerState
│   ├── knowledge.py                 # NEW: UserProfile, ChapterPlan, ChapterResult
│   └── prompts_knowledge.py         # NEW: synthesizer/grader/critic prompts
├── tasks/
│   └── knowledge.py                 # NEW: Celery task wrapping the graph
├── routers/v1/
│   └── knowledge.py                 # NEW: FastAPI routes
└── app.py                           # MODIFY: register knowledge router
```

---

## Open-source dependencies to add

```toml
# apps/fastapi/pyproject.toml additions
"crawl4ai>=0.8.0,<0.9",
"genanki>=0.13",             # flashcards → .apkg
"pypandoc>=1.13",            # Python wrapper for pandoc binary
```

System dependencies (Dockerfile): `pandoc`, `texlive-xetex`, `texlive-fonts-recommended` (for PDF export).

---

## Implementation phases (to be broken into step-by-step PRs)

1. **Schemas + state** — `UserProfile`, `KnowledgeDistillerState`, `ChapterPlan`, `ChapterResult`
2. **Tiered ingestion service** — `services/ingestion_docs.py` with `llms_full_txt()`, `context7()`, `crawl4ai()`
3. **Planner node** — decide N ∈ [4, 12], assign files; structured output
4. **Synth + grader + adjustment loop** — Self-Refine, writes 3 files per chapter
5. **Critic node** — RAGAS-style citation + faithfulness verification
6. **Assembler node** — `summary.md`, `DEBT.md`, episodic memory update
7. **Graph wiring** — `graphs/knowledge.py` with dynamic `Send()` fan-out
8. **Celery task** — `tasks/knowledge.py`, checkpointing
9. **FastAPI router** — create/get/stream/tree/chapter/export/delete endpoints
10. **Export service** — Pandoc pipeline for PDF/HTML/EPUB + genanki for Anki
11. **End-to-end test** — run against a small real framework (e.g., `jinja2` or `pydantic`)

---

## References

- [LangChain DeepAgents Deploy announcement (Apr 9, 2026)](https://www.langchain.com/blog/deep-agents-deploy-an-open-alternative-to-claude-managed-agents)
- [LangGraph orchestrator–worker with dynamic Send() fan-out](https://ai.plainenglish.io/built-with-langgraph-31-orchestrator-worker-design-pattern-aa4ed663fc17)
- [zylos.ai: LLM-as-Judge in production (Apr 10, 2026)](https://zylos.ai/research/2026-04-10-llm-as-judge-production-agent-verification-2026)
- [llms.txt hub — 1,263 sites publishing llms-full.txt](https://llmstxthub.com/)
- [Crawl4AI v0.8 deep crawling](https://docs.crawl4ai.com/core/deep-crawling/)
- [Pandoc v3.9 user guide (Mar 19, 2026)](https://pandoc.org/demo/example2.html)
- Companion: [`KNOWLEDGE-DISTILLER-WHOLE-DOCS-VARIABLE-TONE.md`](./KNOWLEDGE-DISTILLER-WHOLE-DOCS-VARIABLE-TONE.md)
- Companion: [`STUDY-GENERATOR-ADAPTIVE-GRADER.md`](./STUDY-GENERATOR-ADAPTIVE-GRADER.md)
- Companion: [`INTEGRATION-PATTERN-DeepAgents-LangGraph.md`](./INTEGRATION-PATTERN-DeepAgents-LangGraph.md)
- Context: [`NVIDIA-NIM-FREE-MODELS-OPENCLAUDE.md`](./NVIDIA-NIM-FREE-MODELS-OPENCLAUDE.md) (GLM-5 deprecation, model choices)
