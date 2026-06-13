# Research Radar — Agent Graph & Architecture (2026-06-11)

**Status:** authoritative reference for the **shape** of the Research Radar
agent. Captures the orchestrator → subagents → stores topology, the
per-phase contracts, the cross-cutting middleware, and the data shapes
that flow between phases.

**Cross-references:**
- [`RESEARCH-RADAR-DESIGN-2026-06-10.md`](./RESEARCH-RADAR-DESIGN-2026-06-10.md) — the *why* (product, scope, free-tier rationale)
- [`RESEARCH-RADAR-NEXT-STEPS-2026-06-11.md`](./RESEARCH-RADAR-NEXT-STEPS-2026-06-11.md) — the *what next* (file paths, implementation order, acceptance checks)
- [`CODE-CONVENTIONS.md`](./CODE-CONVENTIONS.md) — module file split + frozen-dataclass rules
- [`CODE-ORGANIZATION-SOTA-2026-05-20.md`](./CODE-ORGANIZATION-SOTA-2026-05-20.md) — `apps/<svc>/{domains,infra,…}` layout

---

## 0. TL;DR

A **DeepAgents** orchestrator running inside a Celery task drives **six
sequential phases**. Two of the phases (1 — discovery, 3 — deep_read) are
**parallel fan-outs**; two are **pure functions** (2 — triage,
4 — graph_build); one (5 — synthesis) does GraphRAG over Neo4j + Qdrant;
one (6 — report) renders and persists. The orchestrator never calls MCP
tools itself — that's exclusively the discovery subagents' job, and they
call via a `fastmcp.Client` adapter that wraps each remote tool as a
LangChain `BaseTool`.

The architectural payoff is **cross-source dedup by `arxiv_id`** — the
same paper found by arxiv + semantic_scholar + huggingface_daily_papers +
hn collapses to one node in Neo4j whose signals (HN points, HF upvotes,
citations) are merged across sources.

---

## 1. The graph

```
                                  ┌─────────────────────────────────────┐
                                  │  POST /v1/rr/scan { profile_id }    │
                                  └─────────────────┬───────────────────┘
                                                    ▼
                                  ┌─────────────────────────────────────┐
                                  │  Celery: run_radar_scan             │
                                  │  queue=rr · soft 1800 · hard 2100   │
                                  └─────────────────┬───────────────────┘
                                                    ▼
        ╔═══════════════════════════════════════════════════════════════════════════╗
        ║              ORCHESTRATOR  ── deepagents.create_deep_agent                ║
        ║  ────────────────────────────────────────────────────────────────         ║
        ║  •  decomposes scan into 6 phases · routes work to subagents              ║
        ║  •  checkpointer: AsyncPostgresSaver (resume across worker restart)       ║
        ║  •  shared virtual FS:  state.fs[arxiv_id] = extraction.json              ║
        ║  •  emits SSE phase events → Redis pub/sub → FastHTML                     ║
        ║  •  model: 🔴 strong (Cerebras Llama 3.3 70B)                             ║
        ╚═════════════════════════════════════╤═════════════════════════════════════╝
                                              │
═══ PHASE 1 ═════ DISCOVERY ══ parallel fan-out × 4 sources · isolated context each ═══
                                              │
         ┌────────────────┬───────────────────┼───────────────────┬─────────────────┐
         ▼                ▼                   ▼                   ▼                 ▼
   ┌──────────┐    ┌──────────────┐   ┌─────────────────────┐  ┌──────────────┐
   │discovery │    │discovery     │   │discovery            │  │discovery     │
   │  _arxiv  │    │  _semantic   │   │  _huggingface       │  │  _hn         │
   │          │    │  _scholar    │   │  _daily_papers      │  │              │
   │ 🟢 cheap │    │ 🟢 cheap     │   │ 🟢 cheap            │  │ 🟢 cheap     │
   └────┬─────┘    └──────┬───────┘   └──────────┬──────────┘  └──────┬───────┘
        │ arxiv_search    │ s2_search            │ hf_daily_papers_   │ hn_search
        │                 │                      │     search         │
        ▼                 ▼                      ▼                    ▼
   ════════════════════════════════════════════════════════════════════════════════
            MCP boundary  ──  fastmcp.Client  →  LangChain BaseTool adapter
            (Streamable-HTTP · http://coelhonexus-fastmcp:8000/mcp/)
   ════════════════════════════════════════════════════════════════════════════════
                              │
                              ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │   apps/fastmcp/   ── peer app (Uvicorn · Streamable-HTTP)                  │
   │                                                                            │
   │   ┌─────────────┐ ┌─────────────┐ ┌──────────────────────┐ ┌────────────┐  │
   │   │arxiv_search │ │ s2_search   │ │hf_daily_papers_      │ │ hn_search  │  │
   │   │             │ │             │ │   search             │ │            │  │
   │   │ 3.0s gap    │ │ 3.0/1.0s    │ │  1.0s gap            │ │ 0.5s gap   │  │
   │   └─────────────┘ └─────────────┘ └──────────────────────┘ └────────────┘  │
   │                                                                            │
   │  Middleware (auto-applied to every tool):                                  │
   │   • TelemetryMiddleware  →  OTel span (mcp.tool.*) → Alloy gRPC + LangFuse │
   │   • RateLimitMiddleware  →  per-tool min-interval gate                     │
   │   • CredentialsReader    →  Fernet+MinIO BYOK keys → os.environ            │
   └────────────────────────────────────────────────────────────────────────────┘
        │                 │                      │                    │
        ▼                 ▼                      ▼                    ▼
   arxiv.Paper        s2.Paper             hf.Paper                hn.Hit
   (50-200)           (50-200)             (today's 5-15)          (50-200;
                                                                    ~3-5% carry
                                                                    arxiv_id)
        └─────────────────┴────────────┬─────────┴────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │  domain.normalize()  ──►  NormalizedPaper    │   (PURE)
                │  domain.dedup_by_arxiv_id()                  │
                │    ↳ same arxiv_id from N sources collapses; │
                │      max(hn_points), max(hf_upvotes),        │
                │      merge categories — the cross-source     │
                │      architectural payoff                    │
                └──────────────────────┬───────────────────────┘
                                       ▼
                          (~100-500 unique candidate papers)

═══ PHASE 2 ═════ TRIAGE ══ pure function · no LLM · no I/O ═══════════════════════════
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │   triage    ⚪ pure                          │
                │  ──────────────────────────────────────────  │
                │   domain.signal_score(p, profile_emb, now)   │
                │     = w.rel·cosine + w.rec·decay             │
                │     + w.vel·citations/day + w.infl·ratio     │
                │     + w.fit·vertical + w.buzz·log(hn+hf)     │
                │   ──►  top-N  (N = 12-20)                    │
                └──────────────────────┬───────────────────────┘
                                       ▼

═══ PHASE 3 ═════ DEEP_READ ══ parallel fan-out × top-N · virtual-FS offload ═════════
                                       │
              ┌───────────┬─────────────┬─────────────┬───────────┐
              ▼           ▼             ▼             ▼           ▼
          ┌──────┐    ┌──────┐      ┌──────┐      ┌──────┐    ┌──────┐
          │ dr_1 │    │ dr_2 │      │ dr_3 │ ···  │ dr_N │    │  …   │
          │ 🟡   │    │ 🟡   │      │ 🟡   │      │ 🟡   │    │      │
          │medium│    │medium│      │medium│      │medium│    │      │
          └──┬───┘    └──┬───┘      └──┬───┘      └──┬───┘    └──┬───┘
             │           │             │             │           │
             └───────────┴──────┬──────┴─────────────┴───────────┘
                                ▼
            fetch arxiv abs + PDF · extract  {problem · method · math
                                              · how-to-build · money-angle}
                                │
                                ▼
            state.fs[arxiv_id] = extraction.json     ← DeepAgents virtual FS
                                                      (offloads bulk text from
                                                       orchestrator LLM context)

═══ PHASE 4 ═════ GRAPH_BUILD ══ pure I/O · idempotent · MERGE-by-arxiv_id ════════════
                                │
                                ▼
                ┌──────────────────────────────────────────────┐
                │   graph_build    ⚪ no LLM                   │
                │  ──────────────────────────────────────────  │
                │   • embed abstract  →  Qdrant point          │
                │   • Neo4j MERGE (:Paper {id: arxiv_id})      │
                │   • MERGE (:Concept), (:Author), (:Source)   │
                │   • cross-source rels graft onto same Paper  │
                │     node — same id, multiple [:FROM] edges   │
                └────┬────────────────┬────────────────────────┘
                     ▼                ▼
       ┌──────────────────────┐  ┌────────────────────────────────────┐
       │  Qdrant              │  │  Neo4j                             │
       │  collection:         │  │  (:Paper)-[:ABOUT]->(:Concept)     │
       │   radar_papers       │  │  (:Paper)-[:CITES]->(:Paper)       │
       │  vec: 2048d cosine   │  │  (:Author)-[:AUTHORED]->(:Paper)   │
       │  payload idx:        │  │  (:Paper)-[:FROM]->(:Source)       │
       │   arxiv_id, signal,  │  │  constraint: paper_id UNIQUE       │
       │   published          │  │  index: paper_signal, published    │
       └──────────────────────┘  └────────────────────────────────────┘

═══ PHASE 5 ═════ SYNTHESIS ══ GraphRAG over Neo4j + Qdrant ═══════════════════════════
                                │
                                ▼
                ┌──────────────────────────────────────────────┐
                │   synthesis    🔴 strong (Cerebras 70B)      │
                │  ──────────────────────────────────────────  │
                │   • Cypher: emerging concept clusters        │
                │   • Qdrant: NN clusters on this scan         │
                │   • reads state.fs[*] for extraction bodies  │
                │   • LLM: name themes · spot cross-paper      │
                │     convergence · 'what's new this scan'     │
                └──────────────────────┬───────────────────────┘
                                       ▼

═══ PHASE 6 ═════ REPORT ══ pure render + diff + persist ══════════════════════════════
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │   report    🟡 medium                        │
                │  ──────────────────────────────────────────  │
                │   • domain.diff_vs_seen()  →  new / returning│
                │   • render ranked digest cards               │
                │   • Postgres INSERT radar_findings           │
                │   • MinIO PUT rr/scans/{id}/digest.json      │
                │   • Postgres UPSERT radar_seen               │
                └──────────────────────┬───────────────────────┘
                                       ▼
                ┌──────────────────────────────────────────────┐
                │   SSE stream  →  FastHTML /research-radar    │
                │   { phase, percent, message, item? }         │
                └──────────────────────────────────────────────┘

────────────────────────────────────────────────────────────────────────────────────────
  CROSS-CUTTING (every subagent automatically gets these)

  Observability      every subagent → OTel span `rr.subagent.{name}` · LangFuse trace
  Checkpointer       AsyncPostgresSaver — Celery worker restart resumes mid-scan
  Virtual FS         deep_read writers · synthesis reader · evicts bulk text from ctx
  Model rotator      per-subagent model= param wires into rotator (free-tier-only)
  Error isolation    a subagent crash → orchestrator catches → SSE error event,
                     other subagents continue (DeepAgents' isolated-context payoff)

  Legend             🟢 cheap (Groq/Gemini Flash)   🟡 medium (Llama 70B)
                     🔴 strong (Cerebras 70B)      ⚪ pure (no LLM)
────────────────────────────────────────────────────────────────────────────────────────
```

---

## 2. Why DeepAgents (vs raw LangGraph or vanilla tool-calling loop)

**DeepAgents** gives us three free wins that we'd otherwise have to build:

| Win | What it does | Where it matters in RR |
|---|---|---|
| **Subagents with isolated context** | Each subagent has its own conversation buffer; only its return value reaches the orchestrator | Discovery subagents can each generate hundreds of result objects without polluting the orchestrator's context |
| **Virtual filesystem (`state.fs`)** | Subagents read/write large blobs to a shared FS-like state slot; the orchestrator sees file *names*, not contents | deep_read offloads full paper extractions; synthesis reads only what it needs by `arxiv_id` |
| **Per-subagent model routing** | Each subagent declares its own `model=` parameter | Cheap models for discovery (just shape JSON), strong for synthesis (the headline reasoning), pure for triage/graph_build |

LangGraph still runs underneath — DeepAgents is a layer on top that
declares the orchestrator + subagent pattern as first-class concepts. The
`AsyncPostgresSaver` checkpointer is the standard LangGraph one.

A vanilla tool-calling loop would force us to manage isolated-context
boundaries manually (impossible without subagents) and would put the full
paper text into the orchestrator's context window on every step.

---

## 3. The six phases — contracts & rationale

### Phase 1 — DISCOVERY  (parallel fan-out × 4 sources)

| Field | Value |
|---|---|
| **Subagents** | `discovery_arxiv`, `discovery_semantic_scholar`, `discovery_huggingface_daily_papers`, `discovery_hn` |
| **Parallelism** | 4-way concurrent; each in its own isolated context |
| **Model** | 🟢 cheap (Groq Mixtral / Gemini Flash) — these subagents just shape the search query and route the result |
| **Tool calls** | One MCP tool per subagent, called via `fastmcp.Client` adapter |
| **Input** | The interest profile (verticals, math concepts, since-date) |
| **Output** | Source-specific records (`arxiv.Paper`, `s2.Paper`, `hf.Paper`, `hn.Hit`) — converted to `NormalizedPaper` by the orchestrator at the phase boundary |
| **Failure isolation** | A source-tool 429 or 5xx fails that ONE subagent only; others continue. The orchestrator emits a per-source SSE error event |
| **Why per-source** | Each source has different rate limits, different auth, different query semantics (text vs date axis). One subagent per source is the cleanest cut — and exercises DeepAgents' isolated-context payoff |

**Boundary work (orchestrator, between Phase 1 and Phase 2):**
- `domain.normalize_*()` maps each source-specific shape → `NormalizedPaper`
- `domain.dedup_by_arxiv_id()` collapses cross-source duplicates, merging signals

### Phase 2 — TRIAGE  (pure function)

| Field | Value |
|---|---|
| **Subagent** | `triage` (technically a node, not an LLM subagent) |
| **Model** | ⚪ none — pure deterministic function |
| **Input** | `~100-500 NormalizedPaper` + profile embedding + `SignalWeights` |
| **Output** | `top-N NormalizedPaper` sorted by `signal_score` (N = 12-20, configurable per profile) |
| **Failure mode** | Cannot fail — pure |
| **Why pure** | The scoring function is the contract; no benefit from an LLM judge here. Unit-testable, fast, deterministic, free. Saves $0.001-0.01 per scan compared to an LLM-judged version |

**Implementation:** `apps/fastapi/domains/rr/domain.py::signal_score` (see §5).

### Phase 3 — DEEP_READ  (parallel fan-out × top-N)

| Field | Value |
|---|---|
| **Subagent** | `deep_read` instances (one per paper, fan-out) |
| **Parallelism** | N-way concurrent (N = 12-20) — each one is an isolated DeepAgents subagent |
| **Model** | 🟡 medium (Llama 3.3 70B / DeepSeek) — extraction quality matters but not the headline reasoning |
| **Tool calls** | None — fetches PDF/abs directly via httpx (already have the arxiv URL) |
| **Input** | One `NormalizedPaper` |
| **Output** | `Extraction` JSON written to `state.fs[arxiv_id]` (NOT returned to orchestrator) |
| **Extracted fields** | `problem`, `method`, `math` (key formulas), `how_to_build` (implementation notes), `money_angle` (commercial/business application) |
| **Failure isolation** | A single PDF fetch failure or extraction parse failure drops THAT paper only; others continue |
| **Why virtual FS** | Putting 12-20 extracted JSON blobs into the orchestrator's context wastes tokens. The orchestrator only sees the list of `arxiv_id`s; synthesis reads the FS directly |

### Phase 4 — GRAPH_BUILD  (pure I/O, idempotent)

| Field | Value |
|---|---|
| **Subagent** | `graph_build` (technically a node) |
| **Model** | ⚪ none — no LLM, pure I/O |
| **Input** | All `NormalizedPaper`s + their extractions from `state.fs` |
| **Output** | Side effects only — writes to Neo4j + Qdrant |
| **Idempotency** | `MERGE` (Neo4j) + `upsert` (Qdrant) keyed by `arxiv_id` — re-running is safe |
| **Failure mode** | If Neo4j or Qdrant is down, retry with exponential backoff; non-fatal at scan level |
| **Why now (not after triage)** | Saves the deep_read extraction context — `:Paper` nodes get the extraction summary as a property, enabling future scans to cite without re-extracting |
| **Cross-source graft** | `MERGE (:Paper {id: arxiv_id})` — a paper found via arxiv + s2 + hf + hn becomes ONE node with FOUR `[:FROM]` edges. This is the architectural payoff |

### Phase 5 — SYNTHESIS  (GraphRAG)

| Field | Value |
|---|---|
| **Subagent** | `synthesis` |
| **Model** | 🔴 strong (Cerebras Llama 3.3 70B / NIM 70B+) — the headline reasoning step |
| **Tool calls** | None — direct Cypher + Qdrant queries via Python clients |
| **Input** | Top-N `arxiv_id`s + their extractions read from `state.fs` |
| **Output** | A `SynthesisReport` (themes, cross-paper convergence, what's new vs last scan) |
| **Cypher patterns** | `(:Paper)-[:ABOUT]->(:Concept)<-[:ABOUT]-(:Paper)` — finds papers sharing concepts; clustered by Louvain or label propagation |
| **Qdrant patterns** | NN search over this scan's findings → clusters by embedding similarity |
| **Why strong model** | This is where the radar earns its keep — naming emerging themes from a graph of dozens of papers requires real synthesis ability |

### Phase 6 — REPORT  (pure render + diff + persist)

| Field | Value |
|---|---|
| **Subagent** | `report` |
| **Model** | 🟡 medium — structured rendering only |
| **Input** | `SynthesisReport` + top-N `NormalizedPaper`s + `seen_arxiv_ids` from `radar_seen` table |
| **Output** | `Digest` (JSON) — ranked cards + "new since last scan" section |
| **Side effects** | `INSERT INTO radar_findings`; `PUT s3://coelhonexus/rr/scans/{id}/digest.json`; `UPSERT INTO radar_seen` |
| **Diff** | `domain.diff_vs_seen(candidates, seen_arxiv_ids) → (new, returning)` — pure |
| **Why split from synthesis** | Synthesis is reasoning; report is rendering + persistence. Different model tier, different concerns. Cosmic Python style — functional core (diff_vs_seen) / imperative shell (Postgres + MinIO writes) |

---

## 4. Cross-source dedup — the architectural payoff

The same paper found by multiple sources collapses to **one node** in
Neo4j with **merged signals**. This is the entire reason the radar is
worth more than 4 disconnected feeds.

```
Real example from a live scan (2026-06-10 EOD):

  HN post:    "Differential Transformer" — 562 points, 134 comments,
              url: https://arxiv.org/abs/2410.05258

  ARXIV:      arXiv:2410.05258 — "Differential Transformer"
              authors, abstract, categories: cs.CL, cs.LG

  S2:         paperId 2410.05258 — 47 citations, 3 influential

  ┌──────────────────────────────────────────────────────────────┐
  │  After domain.dedup_by_arxiv_id() + graph_build:             │
  │                                                              │
  │  Neo4j:                                                      │
  │    (:Paper {                                                 │
  │       id: "2410.05258",                                      │
  │       title: "Differential Transformer",                     │
  │       citations: 47,         ← from S2                       │
  │       influential_citations: 3, ← from S2                    │
  │       hn_points: 562,        ← from HN                       │
  │       hn_num_comments: 134,  ← from HN                       │
  │       categories: ["cs.CL", "cs.LG"]  ← from arxiv           │
  │    })                                                        │
  │       │                                                      │
  │       ├─[:FROM]─► (:Source {name: "arxiv"})                  │
  │       ├─[:FROM]─► (:Source {name: "semantic_scholar"})       │
  │       └─[:FROM]─► (:Source {name: "hn"})                     │
  │                                                              │
  │  Qdrant point id: "2410.05258"  ← one vector, not three      │
  └──────────────────────────────────────────────────────────────┘
```

The signal_score then sees a paper with **high citation_velocity AND high
cross_tier_buzz** — a combination no single source could surface. That's
the radar.

---

## 5. Data shapes — the contract between phases

### NormalizedPaper (boundary type, in `domains/rr/entities.py`)

```python
@dataclass(frozen=True, slots=True)
class NormalizedPaper:
    # Identity
    arxiv_id:        str | None       # canonical id; None means no arxiv link found
    title:           str
    abstract:        str
    published:       date | None

    # Authors / categories
    authors:         tuple[str, ...]
    categories:      tuple[str, ...]   # e.g. ("cs.LG", "cs.CL")

    # Per-source signals (merged across sources at dedup time)
    citations:                   int = 0      # S2
    influential_citations:       int = 0      # S2
    hn_points:                   int = 0      # HN
    hn_num_comments:             int = 0      # HN
    hf_upvotes:                  int = 0      # HF Daily Papers

    # Provenance — which sources surfaced this paper
    sources:         frozenset[str] = frozenset()   # {"arxiv", "hn", ...}

    # Computed later (after embedding + scoring)
    embedding:       tuple[float, ...] | None = None   # set by graph_build
    has_code:        bool = False                       # PapersWithCode (v2)
```

### SignalWeights (frozen-dataclass config, in `domains/rr/params.py`)

```python
@dataclass(frozen=True, slots=True)
class SignalWeights:
    relevance:          float = 0.30   # cosine(profile, paper)
    recency:            float = 0.15   # 30-day half-life decay
    citation_velocity:  float = 0.15   # S2 citations / days_since_published
    influential_ratio:  float = 0.10   # S2 infl / total
    vertical_fit:       float = 0.15   # categories ∩ profile.verticals
    cross_tier_buzz:    float = 0.10   # log1p(hn_points) + log1p(hf_upvotes)
    has_code:           float = 0.05   # papers_with_code presence (v2)
```

Defaults tuned for LLMOps/agents/quant verticals; per-profile overrides
live in `radar_profiles.weights` (JSONB).

### Extraction (deep_read output, in `state.fs[arxiv_id]`)

```python
@dataclass(frozen=True, slots=True)
class Extraction:
    arxiv_id:       str
    problem:        str       # 2-3 sentences
    method:         str       # 4-6 sentences
    math:           str       # key formulas (LaTeX) + their role
    how_to_build:   str       # implementation notes; what to wire to what
    money_angle:    str       # commercial / portfolio applicability
    confidence:     float     # 0-1 self-rated extraction confidence
```

### Finding (digest item, in `radar_findings` + `digest.json`)

```python
@dataclass(frozen=True, slots=True)
class Finding:
    arxiv_id:       str
    rank:           int               # 1..N within this digest
    signal:         float
    title:          str
    authors:        tuple[str, ...]
    summary:        str               # 1-line "what's new"
    extraction:     Extraction
    is_new:         bool              # not in radar_seen before this scan
    themes:         tuple[str, ...]   # from synthesis subagent
    sources:        frozenset[str]
```

---

## 6. State flow — sequence view

```
t=0    POST /v1/rr/scan { profile_id }
       └─► Celery enqueues  run_radar_scan(scan_id, profile_id)

t=1    Orchestrator wakes.  State: { profile, scan_id, started_at }
       SSE: { phase: "discovery", percent: 0 }

t=2    4× discovery subagents launched in parallel.
       Each MCP tool call → OTel span auto-created at FastMCP middleware.
       SSE: { phase: "discovery", percent: 25..100, message: "arxiv: 87 results" } …

t=3    Orchestrator: normalize + dedup_by_arxiv_id  → ~300 candidates.
       SSE: { phase: "triage", percent: 0 }

t=4    Triage: signal_score every candidate → sort → top-N.
       SSE: { phase: "deep_read", percent: 0 }

t=5    N× deep_read subagents launched in parallel.
       Each writes to state.fs[arxiv_id].
       SSE: { phase: "deep_read", percent: i/N * 100, message: "extracted: <title>" }

t=6    graph_build: Neo4j MERGE + Qdrant upsert (idempotent).
       SSE: { phase: "graph_build", percent: 0..100 }

t=7    synthesis: GraphRAG over Neo4j + Qdrant + state.fs[*].
       Produces SynthesisReport.
       SSE: { phase: "synthesis", percent: 100 }

t=8    report: diff_vs_seen → render → INSERT radar_findings →
       PUT MinIO digest.json → UPSERT radar_seen.
       SSE: { phase: "done", item: <digest URL> }

       ─── checkpointer persisted state at every phase boundary ───
       (Celery worker can die between any two ts; resume picks up
        from the last completed phase via the scan_id thread_id)
```

---

## 7. Cross-cutting concerns

### 7.1 Observability

- **OTel span per subagent:** `rr.subagent.{name}` — attributes: `scan_id`, `profile_id`, `phase`, `inputs_count`, `outputs_count`, `is_error`.
- **MCP tool spans:** `mcp.tool.{name}` (auto-injected by `TelemetryMiddleware` in fastmcp) — attributes: `mcp.tool.name`, `mcp.tool.input.*`, `mcp.tool.output.*_count`, `mcp.tool.duration_ms`.
- **LangFuse:** dual-exporter (Alloy gRPC + LangFuse HTTP) so spans land in both LangFuse (LLM-centric dashboards) and Tempo (Grafana fan-in, v2).

### 7.2 Checkpointer (resume)

- **`AsyncPostgresSaver`** — LangGraph standard checkpointer, persists to `langgraph_checkpoints` table (auto-created on first write).
- **Same Postgres pool** as the rest of fastapi — DD/YCS already use it.
- **thread_id = scan_id** — each scan is its own LangGraph thread; resume after worker restart is `agent.ainvoke(..., config={"configurable": {"thread_id": scan_id}})`.

### 7.3 Virtual filesystem

- DeepAgents' `state.fs: dict[str, Any]` — backed by the LangGraph state.
- deep_read writes `state.fs[arxiv_id] = extraction.json` (NOT returned to orchestrator).
- synthesis reads `state.fs[arxiv_id]` for whatever subset of papers it needs.
- The orchestrator's LLM context only ever sees the *list of keys*, not the contents.

### 7.4 Model rotator integration

Each subagent's `model=` parameter wires into the existing LLM rotator
(`apps/fastapi/domains/llm/`). The rotator handles:
- Free-tier provider rotation (Groq · Cerebras · Gemini · NIM · OpenRouter free models)
- Per-call bandit selection (FGTS-VA — see `project_rotator_bandit_sota_2026_05_23`)
- Provider-key resolution via Fernet+MinIO BYOK store

| Subagent | Tier | Why |
|---|---|---|
| `discovery_*` | 🟢 cheap | Just shapes the search query |
| `triage` | ⚪ none | Pure function |
| `deep_read` | 🟡 medium | Extraction quality matters |
| `graph_build` | ⚪ none | Pure I/O |
| `synthesis` | 🔴 strong | The headline reasoning step |
| `report` | 🟡 medium | Structured rendering |

### 7.5 Error isolation

DeepAgents' isolated-context model means a subagent crash doesn't poison
the orchestrator's context. The orchestrator catches the subagent
exception, emits an SSE error event for that specific subagent, and
either:
- **Phase 1 (discovery):** marks that source as failed, continues with the other 3.
- **Phase 3 (deep_read):** drops that one paper, continues with the others.
- **Phase 2/4/5/6:** non-recoverable for this scan; mark scan status=`error` in `radar_scans`.

### 7.6 BYOK credentials

- **LLM provider keys** — `apps/fastapi/domains/llm/credentials/` (existing, Fernet+MinIO).
- **Tool keys** (e.g. `SEMANTIC_SCHOLAR_API_KEY`, future `GITHUB_PAT`) — same store, widened `MANAGED_KEY_ENVS`. FastMCP reads at startup via `apps/fastmcp/infra/credentials/`, injects into `os.environ`.

---

## 8. What's NOT in the graph (deferred to v2)

| Item | Trigger to add |
|---|---|
| **Human-approval gate** before `report` commits | When radar moves from single-user to multi-tenant SaaS |
| **DeepAgents context middleware** (`compress`/`offload`/`cache`) | When deep_read fan-out > 50 papers/scan |
| **Sampling / Elicitation** FastMCP primitives | When an agent flow genuinely needs user prompts mid-run |
| **Beat-scheduled scans** + Novu notifications | When manual triggers feel like friction |
| **Citation-graph traversal** in synthesis | When the radar should track NOT just papers but research lineages |
| **PDF caching** in MinIO | When deep_read re-runs fetch the same PDFs |
| **GraphRAG over historical scans** | When cross-scan trend analysis becomes a feature |
| **Elasticsearch full-text** for the digest | When users want to search past digests by keyword |
| **Cytoscape graph viz** in FastHTML | When the digest-card UX feels insufficient |

---

## 9. DeepAgents features — adopted vs roadmap

DeepAgents v0.6's `create_deep_agent` signature exposes 17 parameters. The
radar uses 5 today; the rest are quality-of-life features we'll opt into
as the agent matures. Captured here so they don't get lost when we revisit
DeepAgents-specific affordances in v2+.

### 9.1 What we use today

| Parameter | Used by | Notes |
|---|---|---|
| `model` | orchestrator | `_RotatorAutoRetryRouter` (ChatLiteLLMRouter subclass) — auto-learns NIM 404 arms |
| `tools` | orchestrator-level | `triage_candidates`, `graph_build_papers` (deterministic phases) |
| `system_prompt` | orchestrator | The 6-phase sequencing prompt |
| `subagents` | 7 subagents | 4 discovery + deep_read + synthesis + report; per-subagent `model=` placeholder for tier routing |
| `checkpointer` | LangGraph state | `AsyncPostgresSaver` reusing planner's pool |

### 9.2 `.md` Skills — `create_deep_agent(skills=[...])`

Reusable capability bundles defined as Markdown files. Each skill is a
self-contained "how to do X" that the agent loads alongside its system
prompt. For RR, candidates extracted from current inline prompts:

| Skill `.md` | Content | Replaces |
|---|---|---|
| `paper_extraction.md` | The 5-field shape + confidence rubric the deep_read subagent emits | Inlined in `DEEP_READ_SYSTEM_PROMPT` |
| `cross_paper_synthesis.md` | How to spot convergence vs coincidence, theme-naming guidelines | Inlined in `SYNTHESIS_SYSTEM_PROMPT` |
| `digest_rendering.md` | The canonical digest item shape + summary tone rules | Inlined in `REPORT_SYSTEM_PROMPT` |
| `arxiv_query_shaping.md` | When to use `submittedDate` vs `relevance`, category guidance | Duplicated across 4 discovery prompts |
| `rotator_etiquette.md` | "After a 404, mark the model dead and rebuild" — even though `_RotatorAutoRetryRouter` does this, the skill bundles the rationale for future contributors | New — captures the rotator-resilience design |

**Why this is worth it:** the same skill loads into multiple subagents
(e.g., the `digest_rendering` skill could ground both the `report`
subagent AND a future "explain a paper" FastHTML affordance). Today
those would diverge — one in Python `system_prompt`, one in JS strings.

### 9.3 `.md` Memory — `create_deep_agent(memory=[...])`

Cross-scan / cross-conversation persistent state surfaced to the
orchestrator as Markdown. Different from the per-scan virtual fs in
that memory PERSISTS across scans + sessions. For RR:

| Memory `.md` | Captures | Powers |
|---|---|---|
| `operator_profile.md` | Verticals + weight overrides + "what I built last week" — evolves through use | Triage's per-profile signal weighting (architecture-doc §2.4.3 `radar_profiles.weights`) |
| `themes_seen.md` | Theme names emitted by past synthesis runs | Synthesis can say "we've covered constrained decoding 3 weeks running; flag what's NEW" instead of re-discovering the same themes every scan |
| `radar_calibration.md` | When the operator marks a paper "not interesting" | Triage learns vertical-fit drift without us writing an explicit feedback-loop pipeline |

**Why this is worth it:** the radar gets smarter the more you use it
without us writing a learning loop. The `SignalWeights` dataclass
captures the static defaults; memory captures the evolving overrides.

### 9.4 Other DeepAgents features on the roadmap

| Parameter | Value for RR | Priority |
|---|---|---|
| `response_format` | Enforce the digest JSON schema at the LLM layer — eliminates the "report subagent forgot to include `extraction`" class of bug | **High** — once the basic loop is green |
| `cache` (`BaseCache`) | LLM response cache — re-running the same scan in dev gives instant results | Med — dev ergonomics |
| `middleware` (`AgentMiddleware`) | Custom RR phase-event emission DIRECTLY from the agent layer (instead of just Celery task-boundary emits in step 5) — would give SSE phase-level granularity matching the architecture doc's `discovery → triage → deep_read → graph_build → synthesis → report → done` shape | Med — UX win on the FastHTML SSE stream |
| `interrupt_on` | Human-in-the-loop "approve before persisting digest" gate | Low — single-user single-profile today; matters when multi-tenant |
| `store` (`BaseStore`) | Cross-thread LangGraph store — lets memory `.md` files be QUERYABLE, not just static. Couples with §9.3 | Med — when memory grows past 3 files |
| `permissions` (`FilesystemPermission`) | Restrict which subagents can write to fs/digest.json — stops a hallucinating subagent from corrupting state | Low — defense in depth, not a blocker |
| `response_format` + structured outputs Pydantic | Combined with `ResponseFormat[T]`, the agent returns `T` directly — would make `_run_radar_scan_async` simpler (no JSON parse + Finding construction) | Med-High — pairs with response_format above |
| `backend` (sandbox/filesystem) | Currently default `StateBackend` (in-memory virtual fs). A `FilesystemBackend` would persist fs across scans — but we already do that via Postgres + MinIO at the application boundary, so likely overkill | Low |
| `name` | Cosmetic — for LangFuse traces | Trivial — flip on at any time |

### 9.5 Roadmap placement

| Phase | DeepAgents features added |
|---|---|
| **Step 5 (current)** | nothing — wrestling rotator + LLM correctness; no DeepAgents tweaks |
| **Step 6** | FastHTML UI — no agent changes |
| **Step 7 / phase B** | `.md` skills — extract `paper_extraction.md` + `digest_rendering.md` + `cross_paper_synthesis.md` from current `prompts.py`. ~2-day refactor, no behavior change. Pure cleanup + reusability win |
| **Step 8 / phase C** | `response_format` + Pydantic for the digest — eliminates JSON parse fragility. Also wire `middleware` for per-phase SSE events |
| **Step 9 / phase D** | `.md` memory + `BaseStore` — the radar starts learning across scans |
| **v2 (post-MVP)** | `interrupt_on` (multi-tenant), `permissions`, `cache` (when LangFuse trace inflation becomes painful) |

---

## 10. Implementation pointers

Files to create (in implementation order — matches NEXT-STEPS §2.3.1):

```
apps/fastapi/domains/rr/
├── __init__.py
├── entities.py                NormalizedPaper, Extraction, Finding
├── domain.py                  signal_score, dedup_by_arxiv_id, diff_vs_seen
├── schemas.py                 Pydantic ScanRequest, ScanResult, RadarItem
├── params.py                  SignalWeights, AgentParams (frozen dataclasses)
├── keys.py                    MinIO key builders for digest snapshots
├── errors.py                  RR-specific exception types
├── service.py                 Neo4j + Qdrant + Postgres I/O
├── state.py                   LangGraph state (if domain-level needed)
├── task.py                    Celery run_radar_scan
└── agent/
    ├── __init__.py
    ├── graph.py               build_radar_agent() — DeepAgents harness
    ├── state.py               TypedDict for the agent state
    ├── mcp_client.py          fastmcp.Client → LangChain BaseTool adapter
    ├── prompts.py             Orchestrator + per-subagent prompts
    ├── params.py              Per-subagent AgentParams
    ├── keys.py                SUBAGENT_NAMES tuple
    └── subagents/
        ├── __init__.py
        ├── discovery/         (× 4 — one per source)
        ├── triage/            (pure node, no LLM)
        ├── deep_read/
        ├── graph_build/       (pure node, no LLM)
        ├── synthesis/
        └── report/

apps/fastapi/api/v1/rr/
└── scan/
    ├── __init__.py
    ├── router.py              POST /scan, GET /scan/{id}, GET /scan/{id}/events
    └── schemas.py

apps/fasthtml/features/rr/
├── __init__.py
├── routes.py                  /research-radar + SSE relay
├── body.py                    profile editor + scan trigger + digest area
└── components.py              paper_card, progress_bar, concept_chip
```

See [`RESEARCH-RADAR-NEXT-STEPS-2026-06-11.md`](./RESEARCH-RADAR-NEXT-STEPS-2026-06-11.md)
§5 "The first hour of tomorrow" for the exact 5-step recipe to bring the
agent skeleton up.

---

## Sources

- [`RESEARCH-RADAR-DESIGN-2026-06-10.md`](./RESEARCH-RADAR-DESIGN-2026-06-10.md)
- [`RESEARCH-RADAR-NEXT-STEPS-2026-06-11.md`](./RESEARCH-RADAR-NEXT-STEPS-2026-06-11.md)
- [DeepAgents — orchestrator + subagents + virtual FS](https://docs.langchain.com/oss/python/deepagents/overview)
- [LangGraph AsyncPostgresSaver](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres)
- [FastMCP — Client, Middleware, Streamable-HTTP](https://gofastmcp.com/)
- Live cluster validation (2026-06-10/11) — 4 source tools tested end-to-end with cross-source `arxiv_id` extraction proven on real HN data
