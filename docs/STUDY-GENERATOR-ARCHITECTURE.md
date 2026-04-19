# Study Generator — Architecture

> **⚠️ SUPERSEDED 2026-04-19.** Canonical design is now [`KNOWLEDGE-DISTILLER-ARCHITECTURE.md`](./KNOWLEDGE-DISTILLER-ARCHITECTURE.md). This document is kept for historical reference only.

**Status:** Historical design / decision record (superseded)
**Target:** Phase-2 feature on COELHONexus (after DuckDB + LangGraph v1 studies ship manually on OpenClaude)
**Date:** 2026-04-18
**Related docs:**
- [`LEARNING-PROMPT-RAG-ARCHITECTURE.md`](./LEARNING-PROMPT-RAG-ARCHITECTURE.md) — earlier design doc, partially superseded by this one
- [`ADAPTIVE-RAG-ARCHITECTURE.md`](./ADAPTIVE-RAG-ARCHITECTURE.md) — YouTube Adaptive RAG (distinct feature, remains unchanged)
- [`NEXT-STEPS.md`](./NEXT-STEPS.md) — roadmap integration point

---

## TL;DR

Build a fully autonomous **Research-and-Synthesis Pipeline** on COELHONexus that ingests the official documentation of any framework and outputs a complete study folder (manifest, raw docs, 8 chapters, summary) — replacing the manual OpenClaude workflow documented in `~/Workbench/STUDIES/LEARNING_PROMPT.md`.

- **Input:** a framework name (e.g. `duckdb`, `langgraph-v1`, `vllm`)
- **Output:** complete `~/Workbench/STUDIES/<framework>/` tree with `research/raw/*`, `chapter01/`..`chapter08/`, `summary.md`, `DEBT.md`
- **Shape:** linear pipeline with one fan-out step (per-chapter synthesis) — **not** query-driven adaptive RAG
- **Scale:** one Kubernetes Job per study; N studies run concurrently
- **Effort:** ~2 weeks focused implementation (~60-70% infrastructure reuse from existing COELHONexus)

---

## 1. Context and motivation

The `LEARNING_PROMPT.md` protocol (`~/Workbench/STUDIES/LEARNING_PROMPT.md`) defines a compaction-proof, filesystem-first workflow for generating hiring-grade study material from any framework's official docs. It runs today on OpenClaude + SearXNG MCP + Kimi K2.5 (NVIDIA NIM free tier), distributed across four manually-triggered phases:

1. **Phase A — Discovery:** enumerate all docs URLs → `research/manifest.md`
2. **Phase B — Bulk Fetch:** crawl each URL → `research/raw/<slug>.md`
3. **Phase C — Per-Chapter Synthesis:** 8 isolated sessions, one per chapter
4. **Phase D — Final Assembly:** cross-check + `summary.md`

Running this manually on OpenClaude requires a `/clear` between each phase and constant attention. The Study Generator automates it end-to-end as a job: submit a framework name, come back to a finished study folder.

### What this is NOT

- **Not a chat interface.** No user queries, no conversation.
- **Not query-driven RAG.** No retrieval-at-query-time from the user's side.
- **Not an extension of `adaptive.py`.** Adaptive RAG is request/response and query-classified — wrong shape for long-running pipeline execution.

### What this IS

- A **job-style async pipeline**: submit → execute (hours) → retrieve output.
- A **linear orchestration** with one parallel fan-out (per-chapter synthesis).
- Closer to a CI/CD workflow with LLMs at each station than to a chatbot.
- A **separate sibling module** to `graphs/adaptive.py`, sharing infrastructure (FastAPI, K8s, PostgreSQL checkpointer, LiteLLM, Prometheus) but with its own StateGraph.

---

## 2. The 6-step pipeline

```
Input: framework name + (optional) target version
  │
  ▼
[1] DISCOVERY AGENT  (Kimi K2.5)
  ├─ Web search to locate docs root URL
  ├─ Crawl4AI fetches sidebar / sitemap / index pages only
  ├─ Extract every doc URL, tag by section (quickstart, api-ref, how-to, migration, changelog)
  └─ Writes research/manifest.md
  │
  ▼
[2] FETCHER WORKERS  (parallel; no LLM — just Crawl4AI)
  ├─ Per URL: Crawl4AI → clean markdown
  ├─ Writes research/raw/<slug>.md
  ├─ (optional) Chunk + embed to Qdrant for "talk to my studies" Phase-2 feature
  └─ Updates manifest rows to ✅
  │
  ▼
[3] CHAPTER PLANNER  (Kimi K2.5)
  ├─ Reads manifest (URL + section tags + file paths)
  ├─ Assigns each raw file to exactly one chapter (01-08) based on section semantics
  └─ Writes research/plan.json  (chapter → [files])
  │
  ▼
[4] PER-CHAPTER SYNTHESIZERS  (parallel via LangGraph Send(), 8 workers max)
  ├─ Each worker reads ONLY its assigned files (~3-10 per chapter)
  ├─ Writes research/synth/chNN.md (dense notes, code-first)
  ├─ Writes chapterNN/ directory (README.md or .ipynb with REAL USE CASES)
  └─ Every code block carries `# docs: <section> (research/raw/<slug>.md)`
  │
  ▼
[5] CRITIC  (Nemotron 3 Nano — cheap LLM-as-judge)
  ├─ Per chapter: validate every citation resolves to a file in research/raw/
  ├─ Claim-level verification (RAGAS-style) against cited file content
  ├─ Hallucinated API / missing citation → critic_score < 0.85 → loop to [4] for that chapter only
  └─ Emits per-chapter critic_score metric
  │
  ▼
[6] ASSEMBLER  (Kimi K2.5)
  ├─ Reads all chapterNN/ files
  ├─ Cross-reference consistency check
  ├─ Writes summary.md  (index + skill map + earning roadmap + UAE/Singapore/US market positioning)
  └─ Writes DEBT.md  (TODOs, unresolved inconsistencies)
  │
  ▼
Output: ~/Workbench/STUDIES/<framework>/  (complete, portfolio-ready)
```

### Why linear-with-fan-out, not adaptive

| Attribute | Adaptive RAG (`graphs/adaptive.py`) | Study Generator |
|---|---|---|
| Trigger | User query | Framework name |
| Latency target | Seconds-minutes | Hours |
| Output | In-memory answer string | Persistent multi-file tree |
| Retrieval | Query-time over pre-indexed corpus | Ingestion-then-synthesis |
| Classification | FAST/STANDARD/DEEP mode routing | None — pipeline shape is fixed |
| Subagents | Homogeneous (all run `youtube.py`) | Heterogeneous (discovery ≠ fetch ≠ synth ≠ critic) |
| Lifespan | Request-scoped | Job-scoped (resumable via checkpointer) |
| Streaming | Stream tokens to chat | Write files; expose status via API |

Conclusion: **different primitive**. Share infrastructure, not StateGraph semantics.

---

## 3. Per-step detail

### Step 1 — Discovery Agent

**Tools available:** `searxng_web_search`, `crawl4ai_fetch`, `write_file`

**Prompt skeleton:**
```
You are the Discovery agent for a framework study generation pipeline.

Framework: {framework_name}
Target version: {version or "latest stable"}

Your job:
1. Find the official docs root URL via searxng_web_search.
2. Crawl4AI fetch the root + sidebar / sitemap / index pages ONLY (no content pages).
3. Extract every doc URL. Tag each with a section: quickstart, core-concepts, api-reference,
   how-to, integration, migration, changelog, known-issues, other.
4. Write research/manifest.md as a markdown table with columns:
   | URL | Slug | Section | Fetched | Size | File |
   Slugs must be filesystem-safe (lowercase, hyphens). Every sidebar item must appear.
5. Write research/.checkpoint.md with {phase: "discovery-complete", next_phase: "fetch"}.

Stop when manifest is complete. Do NOT fetch content pages.
```

**Output contract:** `research/manifest.md` with ≥1 row per sidebar item.

### Step 2 — Fetcher Workers

**No LLM per fetch.** Fetcher is a simple Crawl4AI microservice wrapped in a LangGraph node. For each ❌ row in manifest:
1. Call `crawl4ai_client.fetch(url)` → markdown
2. Write to `research/raw/<slug>.md`
3. Update manifest row to ✅
4. Optionally: chunk + embed → Qdrant (for Phase-2 cross-study query)

**Parallelism:** `asyncio.gather` with a semaphore (size ~5 to stay within Crawl4AI/SearXNG rate limits). Not bottlenecked by NIM 40 RPM because no LLM calls in this step.

### Step 3 — Chapter Planner

**Tool:** `read_file` on manifest; `write_file` on `plan.json`

**Prompt skeleton:**
```
You are the Chapter Planner. Read research/manifest.md.

Assign each raw file to exactly one chapter (01-08) using this mapping convention:
- chapter01: quickstart, installation, overview, mental-model, comparison pages
- chapter02: core-concepts, primary API reference, fundamental types
- chapter03: advanced API, edge cases, internals
- chapter04: [DOMAIN-SPECIFIC - determined per framework]
- chapter05: production, scale, deployment, performance
- chapter06: integration guides (match with COELHOCloud services when possible)
- chapter07: known issues, anti-patterns, debugging guides
- chapter08: [will be money projects — no raw files assigned; generated from scratch]

Output research/plan.json:
{
  "chapter01": ["quickstart.md", "installation.md", ...],
  "chapter02": [...],
  ...
  "chapter08": []  // empty — generated, not synthesized from docs
}

Every file in manifest must appear in exactly one chapter (except chapter08 which is empty).
```

### Step 4 — Per-Chapter Synthesizers (parallel fan-out)

**LangGraph `Send()` pattern:** dispatch one subagent invocation per chapter, up to 8 parallel.

**Per-chapter worker:**
- Reads only `research/raw/<file>` files listed in `plan.json[chapterNN]`
- Writes `research/synth/chNN.md` (condensed notes, code-first)
- Writes `chapterNN/README.md` (or `.ipynb` for executable content)

**Model routing:**
| Chapter | Model | Rationale |
|---|---|---|
| ch01 (setup, mental model) | Kimi K2.5 | Narrative, comparisons |
| ch02-05 (core API, advanced, domain, scale) | GLM-5.1 | Code-heavy synthesis |
| ch06 (integration with COELHOCloud) | Kimi K2.5 | Requires cross-referencing COELHOCloud configs |
| ch07 (anti-patterns) | Kimi K2.5 | Narrative reasoning |
| ch08 (money projects) | Kimi K2.5 | Strategic + narrative |

**Prompt skeleton (per-chapter):**
```
You are the Chapter {NN} Synthesizer for the {framework} study.

Your input files:
{list of research/raw/*.md files assigned to this chapter}

Your task:
1. Read all input files.
2. Write research/synth/chNN.md with condensed code-first notes (signal only, no prose).
3. Generate chapterNN/README.md following LEARNING_PROMPT format rules:
   - No padding, no "in this chapter we will..."
   - Code first, explanation after
   - Every code block carries `# docs: <section-name> (research/raw/<slug>.md)`
   - REAL USE CASES section at end mapped to UAE/Singapore/US markets

If any claim cannot be traced to an input file, mark `# TODO: verify` instead of inventing it.

Exit when chapterNN/ is written.
```

### Step 5 — Critic

**Model:** Nemotron 3 Nano (cheap, ~$0 on NIM free tier, fast).

**Per-chapter verification:**
1. Parse every `# docs: <section> (research/raw/<slug>.md)` citation
2. Check the cited file exists
3. For a sample of claims (say 5-10 per chapter), verify the claim appears or is derivable from the cited file content (RAGAS-style atomic claim verification)
4. Emit `critic_score` per chapter (0.0-1.0)
5. If `critic_score < 0.85` → trigger re-synthesis of that chapter only (loop back to step 4 with a note about what failed)

Max re-synthesis iterations per chapter: 2. After that, log to `DEBT.md` and continue.

### Step 6 — Assembler

**Model:** Kimi K2.5.

**Output contract:**
- `summary.md` per LEARNING_PROMPT spec (index, skill map, earning roadmap, UAE/Singapore/US market positioning with G42/Stargate UAE/Emirates NBD/DBS/Grab employer fit)
- `DEBT.md` with any unresolved TODOs, flagged inconsistencies, or chapters that failed the critic

---

## 4. Model routing (multi-model via LiteLLM)

All models hit NVIDIA NIM's OpenAI-compatible endpoint at `https://integrate.api.nvidia.com/v1`.

| Role | Model | NIM slug | Why |
|---|---|---|---|
| Orchestrator + Discovery + ch01/06-08 | Kimi K2.5 | `moonshotai/kimi-k2.5` | Agentic specialist, strong tool-calling, 256K context |
| Code-heavy synthesis (ch02-05) | GLM 5.1 | `z-ai/glm-5.1` | 58.4 SWE-Bench Pro (#1 open-source), 71.8 MCP-Atlas |
| Long-context fallback (rare) | Nemotron 3 Super | `nvidia/nemotron-3-super-120b-a12b` | 1M native context, hybrid Mamba-Transformer — only used when a chapter's raw files exceed ~200K combined |
| Critic | Nemotron 3 Nano | `nvidia/nemotron-3-nano-30b-a3b` | Cheap, fast, sufficient quality for claim verification |

**LiteLLM config example:**

```yaml
# apps/fastapi/config/litellm_study.yaml
model_list:
  - model_name: study-orchestrator
    litellm_params:
      model: openai/moonshotai/kimi-k2.5
      api_base: https://integrate.api.nvidia.com/v1
      api_key: os.environ/NIM_API_KEY

  - model_name: study-code-synthesizer
    litellm_params:
      model: openai/z-ai/glm-5.1
      api_base: https://integrate.api.nvidia.com/v1
      api_key: os.environ/NIM_API_KEY

  - model_name: study-long-context
    litellm_params:
      model: openai/nvidia/nemotron-3-super-120b-a12b
      api_base: https://integrate.api.nvidia.com/v1
      api_key: os.environ/NIM_API_KEY

  - model_name: study-critic
    litellm_params:
      model: openai/nvidia/nemotron-3-nano-30b-a3b
      api_base: https://integrate.api.nvidia.com/v1
      api_key: os.environ/NIM_API_KEY

router_settings:
  routing_strategy: simple-shuffle  # no routing needed; explicit per-call model selection
  num_retries: 3
  timeout: 600
```

**Rate limit note:** NIM free tier is 40 RPM *per model*, not per account. Running multiple models in parallel = separate rate limit buckets = effective ~160 RPM across all four slugs. This is the key reason multi-model routing is usable on free tier.

---

## 5. State model

```python
# apps/fastapi/graphs/study_pipeline/state.py
from typing import Annotated, Literal, Optional
from pydantic import BaseModel
from langgraph.graph.message import add_messages

PhaseStatus = Literal["pending", "running", "complete", "failed"]

class ChapterState(BaseModel):
    number: int                    # 1..8
    assigned_files: list[str]      # paths under research/raw/
    status: PhaseStatus
    critic_score: Optional[float] = None
    retry_count: int = 0
    output_path: Optional[str] = None  # chapterNN/

class StudyState(BaseModel):
    # Inputs
    framework: str
    version: Optional[str] = None
    docs_root_url: Optional[str] = None

    # Paths
    study_root: str                # ~/Workbench/STUDIES/<framework>/
    manifest_path: str             # <study_root>/research/manifest.md
    plan_path: str                 # <study_root>/research/plan.json

    # Phase tracking
    current_phase: Literal[
        "discovery", "fetch", "plan", "synthesize", "critic", "assemble", "complete"
    ]
    phase_status: dict[str, PhaseStatus]

    # Per-chapter state
    chapters: dict[int, ChapterState]

    # Metrics (emitted to Prometheus)
    total_urls_discovered: int = 0
    total_urls_fetched: int = 0
    total_tokens_consumed: dict[str, int] = {}  # per-model counters
    wall_clock_seconds: float = 0.0

    # Messages (for LangGraph's add_messages reducer)
    messages: Annotated[list, add_messages] = []
```

All mutable progress state is persisted via `AsyncPostgresSaver` — any pipeline restart resumes from the last completed phase.

---

## 6. Module layout

```
apps/fastapi/
├── graphs/                            # LangGraph StateGraphs (formerly agents/)
│   ├── adaptive.py                    # YouTube adaptive RAG — UNCHANGED
│   ├── youtube.py                     # YouTube Q&A RAG pipeline — UNCHANGED
│   ├── helpers.py                     # EXISTING — shared helpers
│   └── study_pipeline/                # NEW module
│       ├── __init__.py
│       ├── graph.py                   # LangGraph StateGraph — 6 nodes
│       ├── state.py                   # StudyState, ChapterState
│       ├── nodes/
│       │   ├── __init__.py
│       │   ├── discovery.py           # Step 1
│       │   ├── fetcher.py             # Step 2 (wraps crawl4ai_client)
│       │   ├── planner.py             # Step 3
│       │   ├── synthesizer.py         # Step 4 — invoked via Send() per chapter
│       │   ├── critic.py              # Step 5
│       │   └── assembler.py           # Step 6
│       └── prompts.py                 # Jinja templates per step
├── services/
│   ├── retriever.py                   # EXISTING — Qdrant (unchanged; optionally used by synthesizer)
│   ├── graph_builder.py               # EXISTING — Neo4j (unchanged; optionally fed by fetcher)
│   ├── grader.py                      # EXISTING — pattern reused by critic.py
│   ├── conversation.py                # EXISTING — unchanged
│   └── crawl4ai_client.py             # NEW — HTTP client for Crawl4AI microservice
├── schemas/
│   ├── state.py                       # EXISTING — AdaptiveRAGState, YouTubeRAGState
│   ├── agents.py                      # EXISTING — Pydantic models for agent I/O
│   └── study.py                       # NEW — StudyRequest/Response, StudyStatus
├── routers/                           # FastAPI HTTP layer
│   └── v1/                            # add studies router here
└── app.py                             # MODIFY: wire studies router
```

**Note on naming:** the `graphs/` folder was renamed from `agents/` in April 2026 to match LangGraph terminology. Both `adaptive.py` and `youtube.py` are LangGraph StateGraphs (`workflow = StateGraph(...)`), not single agents. The study pipeline joins them as a sibling module.

---

## 7. API contract

### `POST /studies` — submit a new study

```http
POST /studies
Content-Type: application/json

{
  "framework": "duckdb",
  "version": "1.1.4",
  "target_docs_url": "https://duckdb.org/docs/stable/",
  "study_root": "/home/rafaelcoelho/Workbench/STUDIES/duckdb",
  "config": {
    "max_chapter_retries": 2,
    "parallel_chapter_synthesis": true,
    "enable_qdrant_indexing": false,
    "enable_neo4j_concept_graph": false
  }
}
```

Response:
```json
{
  "study_id": "8c3a9f12-...",
  "thread_id": "study:duckdb-1.1.4:8c3a9f12",
  "status": "queued",
  "estimated_completion_iso": "2026-04-18T14:30:00Z"
}
```

### `GET /studies/{id}` — check progress

```json
{
  "study_id": "8c3a9f12-...",
  "framework": "duckdb",
  "status": "running",
  "current_phase": "synthesize",
  "progress": {
    "discovery": "complete",
    "fetch": {"done": 87, "total": 104},
    "plan": "complete",
    "synthesize": {"done_chapters": 3, "total": 8, "current_ch": 4},
    "critic": "pending",
    "assemble": "pending"
  },
  "study_root": "/home/rafaelcoelho/Workbench/STUDIES/duckdb",
  "started_at": "2026-04-18T10:05:00Z",
  "wall_clock_seconds": 7142
}
```

### `GET /studies/{id}/logs` — stream logs (SSE)

For live observability during long-running jobs. Not required for MVP.

### `POST /studies/{id}/cancel` — stop a study

Marks the thread as cancelled in Postgres; the next checkpointer poll terminates.

---

## 8. Kubernetes Job deployment

One K8s Job per study. Allows N concurrent studies without resource contention.

```yaml
# terraform/modules/coelhonexus/templates/study-job.yaml.tpl
apiVersion: batch/v1
kind: Job
metadata:
  name: study-${study_id}
  labels:
    app: coelhonexus
    component: study-pipeline
    framework: ${framework}
spec:
  ttlSecondsAfterFinished: 86400        # cleanup 24h after completion
  backoffLimit: 2                        # retry whole study twice on pod failure
  template:
    metadata:
      labels:
        app: coelhonexus
        component: study-pipeline
    spec:
      restartPolicy: OnFailure
      containers:
      - name: study
        image: coelhonexus/fastapi:${image_tag}
        command: ["python", "-m", "apps.fastapi.agents.study_pipeline.run", "${study_id}"]
        env:
        - name: STUDY_ID
          value: "${study_id}"
        - name: NIM_API_KEY
          valueFrom:
            secretKeyRef:
              name: nim-credentials
              key: api_key
        - name: POSTGRES_DSN
          valueFrom:
            secretKeyRef:
              name: postgres-credentials
              key: dsn
        volumeMounts:
        - name: studies-output
          mountPath: /home/rafaelcoelho/Workbench/STUDIES
        resources:
          requests:
            cpu: "500m"
            memory: "2Gi"
          limits:
            cpu: "2"
            memory: "8Gi"
      volumes:
      - name: studies-output
        hostPath:
          path: /home/rafaelcoelho/Workbench/STUDIES
          type: Directory
```

---

## 9. Observability

### Prometheus metrics (emitted by pipeline)

```
coelhonexus_study_jobs_total{framework, status}                  # counter
coelhonexus_study_phase_duration_seconds{framework, phase}       # histogram
coelhonexus_study_urls_fetched_total{framework}                  # counter
coelhonexus_study_urls_failed_total{framework, reason}           # counter
coelhonexus_study_tokens_consumed_total{framework, model, role}  # counter
coelhonexus_study_critic_score{framework, chapter}               # gauge
coelhonexus_study_chapter_retries_total{framework, chapter}      # counter
coelhonexus_study_wall_clock_seconds{framework}                  # gauge
```

### Grafana dashboard

New dashboard: **"Study Generator"**:
- Panel: studies in flight (count by framework, phase)
- Panel: completion rate over time
- Panel: mean critic score per chapter across all studies
- Panel: tokens consumed per model per study (cost audit)
- Panel: phase duration heatmap (find slow phases)

### LangSmith tracing

All LangGraph invocations traced to LangSmith free tier. Thread_id follows `study:{framework}-{version}:{uuid}` for cross-study comparison.

---

## 10. Reuse map

| Layer | Component | Reuse |
|---|---|---|
| **Infra** | FastAPI app (`app.py`) | ✅ Extend with `/studies` routes |
| **Infra** | Kubernetes deployment | ✅ Add Study Job template |
| **Infra** | PostgreSQL (checkpointer + conversation) | ✅ Add `studies` table with same shape as `conversation_history` |
| **Infra** | MinIO | ✅ Use for raw HTML blob archival |
| **Infra** | Prometheus + Grafana | ✅ Add study-specific metrics + dashboard |
| **Framework** | LangGraph StateGraph primitives | ✅ Same library, different graph shape |
| **Framework** | `AsyncPostgresSaver` checkpointer | ✅ Same, different thread_id namespace |
| **Framework** | LiteLLM multi-model routing | ✅ Extend config with 4 new model_name entries |
| **StateGraph** | `adaptive.py` itself | ❌ NOT reused — different paradigm (query-driven vs pipeline) |
| **StateGraph** | `youtube.py` retrieve-grade-generate loop | ❌ NOT reused — wrong shape for synthesis |
| **StateGraph** | Query classification | ❌ NOT needed — input is a framework name |
| **StateGraph** | Conversation history | ❌ NOT needed — job, not chat |
| **Service** | `services/retriever.py` (Qdrant) | ⚠ Optional — only if synthesizer needs vector-similarity file selection |
| **Service** | `services/graph_builder.py` (Neo4j) | ⚠ Optional — nice-to-have for cross-study concept graph |
| **Service** | `services/grader.py` pattern | ✅ Reused by `critic.py` |
| **Service** | Crawl4AI client | ❌ NEW — ~100 lines of HTTP wrapper |

**Infrastructure reuse: ~70%. StateGraph reuse: ~0%. Net new code: ~1500-2500 lines.**

---

## 11. Implementation plan

### Week 0 (before starting): ship 2 manual studies on OpenClaude

**Purpose:** validate LEARNING_PROMPT protocol + produce reference outputs for validation of the automated pipeline.

- DuckDB (in flight)
- LangGraph v1 + DeepAgents

These two studies become:
- Immediate portfolio pieces for applications to G42 / Stargate UAE / DBS / Grab
- Reference outputs: the automated pipeline's DuckDB run must match or exceed these

### Week 1: scaffolding + discovery + fetch

- [ ] Create `graphs/study_pipeline/` module skeleton
- [ ] Define `StudyState`, `ChapterState` Pydantic models
- [ ] Build `crawl4ai_client.py` (deploy Crawl4AI microservice to K3D)
- [ ] Implement `nodes/discovery.py` (step 1)
- [ ] Implement `nodes/fetcher.py` (step 2)
- [ ] Add `POST /studies` + `GET /studies/{id}` to `app.py`
- [ ] Deploy study K8s Job template
- [ ] End-to-end test: discovery + fetch for DuckDB → verify manifest matches manual Week-0 run

### Week 2: planner + synthesizer + critic + assembler

- [ ] Implement `nodes/planner.py` (step 3)
- [ ] Implement `nodes/synthesizer.py` (step 4) with LangGraph `Send()` fan-out
- [ ] Configure LiteLLM multi-model routing
- [ ] Implement `nodes/critic.py` (step 5) with RAGAS-style claim verification
- [ ] Implement `nodes/assembler.py` (step 6)
- [ ] End-to-end test: run full DuckDB study autonomously → compare output to Week-0 reference
- [ ] Fix quality gaps until automated output matches or exceeds manual output

### Week 3 onward: scale

- [ ] Run 5-10 studies in parallel via K8s Jobs: vLLM, Arabic NLP tooling, CUDA basics, Kubeflow update, Ray, DSPy, DeepAgents, MCP SDK, Nemotron deployment, MLflow updates
- [ ] Iterate on prompts based on output quality per framework
- [ ] Publish blog post on rafaelcoelho1409.github.io: "Building a Deep Research Agent for Framework Documentation Ingestion"
- [ ] Use the system itself as a portfolio showpiece for UAE/Singapore interviews

---

## 12. Relationship to LEARNING-PROMPT

`~/Workbench/STUDIES/LEARNING_PROMPT.md` remains the **source of truth for output format and quality standards** — the Study Generator encodes its rules programmatically:

| LEARNING_PROMPT rule | Study Generator enforcement |
|---|---|
| ENTIRE official docs are mandatory input | Discovery agent's manifest must contain every sidebar item; incomplete manifests block Phase B start |
| Every code block carries `# docs: <section> (research/raw/<slug>.md)` | Enforced by synthesizer prompt + verified by critic |
| No padding / "in this chapter we will..." | Enforced in synthesizer prompt |
| REAL USE CASES section at end | Required output field in synthesizer schema |
| Chapter 06 integrates with COELHOCloud | Synthesizer for ch06 reads `~/Workbench/COELHOCloud/` configs |
| UAE / Singapore / US market positioning | Assembler prompt references the market intelligence section of LEARNING_PROMPT |
| Portfolio anchors (COELHO RealTime, COELHO Agents, etc.) | Assembler prompt includes explicit portfolio cross-reference rules |

The Study Generator IS the automated executor of LEARNING_PROMPT. When LEARNING_PROMPT is updated (new market intelligence, refined rules), the Study Generator's prompts are regenerated from it.

---

## 13. Future extensions (Phase 2+)

### Cross-study querying ("talk to my studies")

After running 10+ studies, the accumulated `research/raw/*` corpora can be indexed to Qdrant. This enables:
- Adaptive RAG queries across all frameworks studied
- "What's the difference between LangGraph and DeepAgents?" → query hits both studies' raw docs
- Reuses `graphs/adaptive.py` DEEP mode, pointed at a new `studies_corpus` Qdrant collection

### Concept graph across frameworks

Populate Neo4j with extracted concepts from each framework's raw docs (e.g., `Framework:LangGraph -[HAS_FEATURE]-> Concept:Checkpointer`, `Framework:DeepAgents -[EXTENDS]-> Framework:LangGraph`). Enables queries like "which frameworks implement checkpointing?" as graph traversals.

### Auto-update when frameworks release new versions

Cron job watches GitHub releases pages for each studied framework. On new major version:
1. Diff the old vs new sidebar (which URLs are new/changed)
2. Re-fetch only changed URLs
3. Re-synthesize only affected chapters (diff-driven)
4. Notify via Telegram: "vLLM 0.11 released; chapter03 re-synthesized; review diff."

### Multi-tenant / SaaS mode

If productizing later: per-user study folders in MinIO, per-tenant PostgreSQL schema, auth via existing FastAPI middleware. Pricing tier based on concurrent studies + framework count. This is the commercial product hidden inside your hiring tool.

---

## Appendix A: NVIDIA NIM model selection (April 2026 verified)

Benchmark sources: Artificial Analysis, llm-stats.com, BenchLM, NIM model cards.

| Model | Context | SWE-bench Verified | GPQA Diamond | AIME | MCP-Atlas | Best for |
|---|---|---|---|---|---|---|
| `moonshotai/kimi-k2.5` | 256K | 76.8% | 87.9% (thinking) | ~90% | 29.5% | Orchestration, agentic tool-use |
| `z-ai/glm-5.1` | 202K | ~77% | ~86% | 92.7% | **71.8%** | Code-heavy synthesis, tool-call reliability |
| `nvidia/nemotron-3-super-120b-a12b` | **1M native** | 60.5% | 79.4% | ~95% | — | Long-context retention (Mamba-Transformer) |
| `nvidia/nemotron-3-nano-30b-a3b` | 1M | — | — | — | — | Cheap critic / judge |

Rate limits: 40 RPM per model on free tier. Using 4 different models across pipeline phases = ~160 RPM effective aggregate.

---

## Appendix B: decision log

### Why NOT extend `adaptive.py` with a `DEEP_STUDY` mode

Initially considered adding `DEEP_STUDY` as a fourth mode in `adaptive.py`. Rejected because:
1. `adaptive.py` is request/response; Study is a multi-hour job
2. Query classification has no meaningful role (input is a framework name)
3. Conversation history is irrelevant to job execution
4. Coupling the two features would risk regressions in the working YouTube RAG
5. Separate StateGraphs are easier to reason about, test, and deploy independently

### Why NOT use Dagster or Prefect instead of LangGraph

Considered a pure DAG orchestrator:
- **Pro:** simpler for non-LLM steps (fetcher, planner)
- **Con:** LangGraph's native `Send()` fan-out + `AsyncPostgresSaver` + LLM-tool-call loop handling in critic re-synthesis is custom-built for this; reimplementing in Dagster = more glue code
- **Decision:** LangGraph wins because of the per-chapter retry-loop-with-critic pattern

### Why multi-model (Kimi + GLM + Nemotron) instead of single model

- Kimi K2.5 is strongest at tool-calling orchestration (MCP-Atlas: 29.5, but proven reliable)
- GLM 5.1 is strongest at code-heavy synthesis (MCP-Atlas: 71.8, SWE-Bench Pro #1 open-source)
- Nemotron 3 Nano is 10x cheaper for critic-as-judge role
- NIM's per-model rate limits mean multi-model = effective higher throughput

### Why Crawl4AI and not SearXNG `web_url_read` MCP

- SearXNG MCP is OpenClaude-scoped; Study Generator runs as a K8s Job without OpenClaude
- Crawl4AI is directly callable as a Python library or microservice
- Crawl4AI handles JS-rendered docs sites (many 2026 docs are SPAs) — essential for completeness

### Why no Qdrant indexing in Phase 1

- The Study Generator's internal "which file goes to which chapter" assignment is deterministic (manifest section tags → chapter mapping)
- Synthesizer reads a small set of pre-assigned files per chapter — no similarity search needed
- Qdrant adds value only for Phase-2 cross-study querying, which is out of scope for the initial build
- Keeping the dependency surface minimal speeds Phase 1 delivery

### Why 8 chapters, not N

- Matches LEARNING_PROMPT's 8-chapter structure exactly
- Each chapter has a well-defined thematic intent (Setup → Core → Advanced → Domain → Scale → Integration → Anti-patterns → Money)
- 8 parallel LangGraph `Send()` fan-outs is within practical Kimi K2.5 / GLM 5.1 rate-limit ceilings when combined with the ~30s NIM request latency
