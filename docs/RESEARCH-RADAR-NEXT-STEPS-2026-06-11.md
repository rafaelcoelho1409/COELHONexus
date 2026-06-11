# Research Radar — Continuation Roadmap (2026-06-11)

**Status:** 4 source tools + middleware substrate + Settings-UI BYOK SHIPPED.
Agent orchestrator + stores + UI still to build.

**Cross-references:**
- [`RESEARCH-RADAR-DESIGN-2026-06-10.md`](./RESEARCH-RADAR-DESIGN-2026-06-10.md) — authoritative design (the *why*).
- [`CODE-CONVENTIONS.md`](./CODE-CONVENTIONS.md) — module file split + dataclass rules.
- [`CODE-ORGANIZATION-SOTA-2026-05-20.md`](./CODE-ORGANIZATION-SOTA-2026-05-20.md) — `apps/<svc>/{domains,infra,…}` layout.

This doc is **the implementation order + spec to follow tomorrow**. Every
remaining v1 task lists exact file paths, the established convention to copy,
the consumer that will use the output, and the acceptance check.

---

## 0. TL;DR — start tomorrow with **§2.3 (DeepAgents orchestrator skeleton)**

The 4 tools have nothing driving them yet. The single highest-leverage move
is to scaffold the agent end-to-end (orchestrator + ONE subagent that calls
arxiv) so the radar has a *consumer*. Stores and UI follow naturally from
the agent's output shape. Order:

1. **§2.3.1** Agent skeleton (orchestrator + discovery subagent) — closes the loop with arxiv as proof
2. **§2.4** Stores (Neo4j + Qdrant + Postgres tables) — for the agent to write into
3. **§2.5** Pure `domain.signal_score` — for the triage subagent
4. **§2.3.2-2.3.6** Remaining subagents (triage · deep-read · graph-build · synthesis · report)
5. **§2.6** API + Celery task + SSE — agent driven by HTTP
6. **§2.7** FastHTML page — renders agent output

§2.1 (tool #5 = github WRAP), §2.2 (Resources / Prompts / Composition / Auth),
and §2.8-2.9 (LangFuse dashboard + polish) are **parallel work** — can land
any time after the agent loop closes.

---

## 1. Current state — what's shipped

### 1.1 Infrastructure

```
apps/fastmcp/                            ── peer app (3rd alongside fastapi · fasthtml)
├── server.py                            init_otel + register middleware + register domains
├── infra/
│   ├── otel/                            Alloy gRPC + LangFuse HTTP dual exporters
│   └── credentials/                     Sync MinIO+Fernet READER → injects user keys into os.environ at startup
├── middleware/
│   ├── telemetry.py                     OTel span per tool call (auto-spans every new tool)
│   └── ratelimit.py                     Per-tool min-interval gate (auto-applies to every new tool)
└── domains/rr/
    ├── server.py                        register(mcp) for all RR tools
    └── tools/
        ├── arxiv/                       (text query · Atom XML parser · phrase-quoting fix)
        ├── semantic_scholar/            (text query · BYOK x-api-key · TLDR + influential_citations)
        ├── huggingface_daily_papers/    (date axis · curated feed · community upvotes)
        └── hn/                          (text query · Algolia · URL→arxiv_id extractor for cross-source dedup)
```

### 1.2 FastAPI side

```
apps/fastapi/
├── api/v1/router.py                     mounts /rr → rr_router
├── api/v1/rr/
│   ├── __init__.py                      aggregates tool_credentials
│   └── tool_credentials/                BYOK API for FastMCP tool keys (set · delete · test)
└── domains/llm/credentials/keys.py      MANAGED_KEY_ENVS includes SEMANTIC_SCHOLAR_API_KEY
```

### 1.3 FastHTML side

```
apps/fasthtml/
├── features/common/routes.py            /research-radar route (placeholder _Shell)
├── features/settings/page.py            includes "Source Tool Keys" section
├── static/js/settings_tool_keys.js      LLM-style card · Save/Test/Delete · 200 LOC
└── static/css/settings/settings.css     +60 LOC: .settings-tool-keys + .tk-* + .set-section-title
```

### 1.4 Helm + Skaffold

- `k8s/helm/values.yaml` has `fastmcp:` block (ClusterIP:8000 · /health probes).
- `k8s/helm/templates/fastmcp/{deployment,service,configmap}.yaml` mirror the macros.
- `skaffold.yaml` has `localhost:5001/coelhonexus-fastmcp` artifact + port-forward 23024.
- ArgoCD prod port-forward script extended (23004 → fastmcp).
- Port-forward pairing: **2302X ↔ 2300X** (fastapi 0 · flower 2 · fasthtml 3 · fastmcp 4).

### 1.5 Verified end-to-end on the live cluster

- All 5 tools (`ping` + 4 source tools) in `tools/list`.
- Per-tool rate-limit enforcement (3s/0.5s/1s gaps observable in logs).
- `Context` server→client streaming (`ctx.info` reaches client mid-call).
- Cross-source arxiv_id extraction from real HN posts (5/118 HN hits carried arxiv_id; the mechanism is proven even when today's slice has 0 academic overlap).

---

## 2. v1 — remaining work, in implementation order

### 2.1 Tool #5 — `github` (WRAP via `create_proxy`) — PARALLEL, optional v1

**Why:** the only FastMCP primitive still unexercised that earns its keep
with real engineering — wrapping `github/github-mcp-server` (Go-native,
official, complex GitHub OAuth) is genuinely faster than reimplementing.

**Location:**

```
apps/fastmcp/domains/rr/tools/github/
├── __init__.py
├── config.py             # GHConfig — upstream URL, auth env, timeout
├── tool.py               # register(mcp): wraps via fastmcp.proxy.create_proxy(client)
└── (no service/domain/schemas — the proxy forwards the upstream tool list verbatim)
```

**Key design call:**
- Use `fastmcp.Client` with `BearerAuth(token=os.environ["GITHUB_PAT"])` to authenticate to the upstream server.
- `create_proxy(client, name="github")` exposes upstream tools (search · stars · trending · …) through our root server with the existing telemetry + rate-limit middleware automatically wrapping each call.
- This means tool #5 only adds ~30 LOC, exercising the Proxy primitive against a real complex auth surface.

**Settings UI:** add `GITHUB_PAT` to `MANAGED_KEY_ENVS` + a new
`ToolKeyDef` row in `api/v1/rr/tool_credentials/params.py`. The Settings UI
auto-renders it once those two edits land — no extra UI work.

**Acceptance:** `tools/list` shows N upstream GitHub tool names prefixed
with `github_`; calling one returns structured data; the telemetry +
rate-limit middleware wrap the call (visible in LangFuse).

### 2.2 FastMCP primitives still un-exercised — PARALLEL, recommended

#### 2.2.1 Resources (radar://...)

**Why:** lets the agent *load context* without spending a tool call. The
ranked digest snapshot + concept lookups are perfect Resource candidates.

**Location:**
```
apps/fastmcp/domains/rr/resources/
├── __init__.py
├── latest_digest.py      # @mcp.resource("radar://latest_digest"); reads MinIO snapshot
└── concept.py            # @mcp.resource("radar://concept/{name}"); reads Neo4j subgraph
```

**Registration:** add `from .resources import latest_digest, concept`
in `domains/rr/server.py::register()`.

**Acceptance:** MCP client calls `read_resource("radar://latest_digest")` and
gets the most recent digest JSON; calling `radar://concept/SVD` gets the
Neo4j concept node + related papers as JSON.

#### 2.2.2 Prompts (slash-commands)

**Why:** these become user-invokable from a FastHTML "command palette"
later. Different from internal node prompts — these are USER-controlled.

**Location:**
```
apps/fastmcp/domains/rr/prompts/
├── __init__.py
└── digest_today.py       # @mcp.prompt("/digest_today") returns a templated prompt
```

**Acceptance:** MCP client's `list_prompts()` returns `digest_today`;
calling it returns a parametrized prompt string.

#### 2.2.3 Server composition (`mount()`)

**Why:** the design doc calls for `mount(rr_mcp, prefix="rr")` when
`domains/` grows beyond one. Today we use the `register(mcp)` pattern
(works, ships, mirrors FastHTML). Swap to `mount()` when adding a 2nd
domain (e.g. when DD or YCS expose MCP tools).

**Trigger:** delay until the *second* `domains/` package exists. No
premature refactor.

#### 2.2.4 Auth (JWT one-liner)

**Why:** when the server faces beyond ClusterIP (e.g. external MCP clients
testing it from the host, or a future public B2B surface).

**Location:** one line in `apps/fastmcp/server.py`:
```python
from fastmcp.server.auth import JWTVerifier
mcp = FastMCP("coelhonexus-mcp", auth=JWTVerifier(public_key=..., audience=...))
```

**Trigger:** when you want to host an Inspector-driven demo from outside the
cluster, OR when DD/YCS migrate from in-cluster Python-import to MCP-client.

---

### 2.3 DeepAgents orchestrator — `apps/fastapi/domains/rr/agent/`

**This is the big one.** Build incrementally — orchestrator + ONE subagent
calling arxiv first; then add subagents one at a time, testing each.

#### 2.3.1 Skeleton — orchestrator + discovery subagent (START HERE)

**Layout:**
```
apps/fastapi/domains/rr/
├── __init__.py
├── agent/
│   ├── __init__.py
│   ├── graph.py                 # build_radar_agent() — DeepAgents harness; orchestrator + subagent roster
│   ├── state.py                 # TypedDict for the LangGraph state
│   ├── mcp_client.py            # fastmcp.Client → http://coelhonexus-fastmcp:8000/mcp/
│   ├── prompts.py               # Orchestrator prompt + per-subagent prompts
│   ├── params.py                # @dataclass(frozen=True) AgentParams (cap depths, n_max defaults)
│   ├── keys.py                  # SUBAGENT_NAMES tuple (discovery · triage · deep_read · synthesis · report)
│   ├── subagents/
│   │   ├── __init__.py
│   │   └── discovery/
│   │       ├── __init__.py
│   │       ├── prompts.py       # System prompt for the discovery subagent
│   │       ├── service.py       # async drive(mcp_client, profile) → list[Paper]
│   │       └── (no domain.py needed — discovery is pure orchestration)
│   └── observability/
│       └── spans.py             # @traced("rr.<node>") wrapper
├── domain.py                    # PURE: signal_score · dedup · diff_vs_seen (see §2.5)
├── service.py                   # I/O glue: Neo4j upsert · Qdrant embed · Postgres write
├── schemas.py                   # Pydantic ScanRequest · ScanResult · RadarItem
├── entities.py                  # @dataclass: NormalizedPaper · Finding (cross-source unified shape)
├── keys.py                      # MinIO key builders for digest snapshots
├── params.py                    # @dataclass frozen: SignalWeights, AgentParams (cross-cutting)
├── errors.py
├── task.py                      # Celery run_radar_scan → drives agent · emits SSE
└── state.py                     # LangGraph state (if needed at domain level)

apps/fastapi/api/v1/rr/
├── __init__.py                  # adds scan router beside tool_credentials
└── scan/
    ├── __init__.py
    ├── router.py                # POST /scan · GET /scan/{id} · GET /scan/{id}/events (SSE)
    └── schemas.py               # ScanRequest body
```

**Day-1 implementation scope:**

```python
# agent/graph.py — DeepAgents 1-subagent harness
from deepagents import create_deep_agent

agent = create_deep_agent(
    instructions="<orchestrator prompt from prompts.py>",
    tools=[],  # The agent doesn't call MCP tools directly; subagents do.
    subagents=[
        {
            "name": "discovery_arxiv",
            "description": "Searches arxiv for papers matching the interest profile.",
            "prompt": DISCOVERY_PROMPT,
            "tools": [arxiv_search_tool],  # MCP client adapter
        },
    ],
    checkpointer=AsyncPostgresSaver(...),  # Postgres for resume
)
```

**MCP client adapter** (`agent/mcp_client.py`):
- Singleton `fastmcp.Client` at `http://coelhonexus-fastmcp:8000/mcp/`.
- Helper that wraps each MCP tool as a LangChain `BaseTool` so DeepAgents'
  subagent registration accepts it.

**Subagent roster — to add incrementally after the skeleton works:**

| # | Subagent | Calls (MCP tools) | Output |
|---|---|---|---|
| 1 | `discovery` (per source: arxiv · s2 · hf · hn) | source tools | candidate `Hit`s |
| 2 | `triage` | (none — pure) | top-N by signal_score |
| 3 | `deep_read` (async, fan-out) | (none — uses arxiv abs URL) | extracted {problem · method · math · how-to-build · money-angle} |
| 4 | `graph_build` | (none — calls Neo4j via state) | upserts to Neo4j + Qdrant |
| 5 | `synthesis` | (none — GraphRAG over Neo4j) | emerging clusters · cross-paper themes |
| 6 | `report` | (none — pure render) | ranked digest + "new since last scan" |

#### 2.3.2 Async deep_read with virtual filesystem

DeepAgents' virtual FS lets the deep_read subagent offload full paper text
out of the LLM context. Implementation: each deep_read subagent invocation
writes its extraction to `state.fs[paper_id]`, the synthesis subagent
reads only the fields it needs.

#### 2.3.3 Postgres checkpointer (resume + diff)

LangGraph's `AsyncPostgresSaver` — same pattern DD's planner/synth already
use (see `apps/fastapi/domains/dd/planner/runtime/`). Reuse the existing
Postgres pool. Schema lives in `langgraph_checkpoints` table (auto-created
by saver on first write).

#### 2.3.4 Per-subagent model routing

DeepAgents accepts a `model` parameter per subagent. Wire to the rotator
so each subagent picks the right tier:

| Subagent | Model tier | Rationale |
|---|---|---|
| `discovery` | cheap (Groq Mixtral / Gemini Flash) | structured-output tool routing |
| `triage` | none (pure function) | no LLM needed |
| `deep_read` | medium (Llama 3.3 70B / DeepSeek) | extraction quality matters |
| `synthesis` | strong (Cerebras Llama 3.3 / NIM 70B+) | the headline reasoning step |
| `report` | medium (same as deep_read) | structured rendering |

Use the existing rotator's `chat_judge_bandit_async` for model selection.

#### 2.3.5 Human-approval gate (defer to v2; document the hook)

DeepAgents supports a `human_in_loop` config that pauses before specific
subagents. **Defer** — radar is single-user, the operator trusts its own
agent. Leave a TODO comment marking the integration point in
`agent/graph.py` so v2 adds it cleanly.

#### 2.3.6 Context-engineering middleware (defer to v2)

DeepAgents' `compress` / `offload` / `cache` middleware. Adds value when
deep_read fan-out gets large (50+ papers/scan). Skip for v1 (small scans);
the FS offload alone is enough.

---

### 2.4 Stores — Neo4j + Qdrant + Postgres + MinIO

#### 2.4.1 Neo4j schema (`apps/fastapi/domains/rr/service.py`)

```cypher
// Nodes
(:Paper {
    id: string,                   // canonical = arxiv_id (no version suffix)
    title: string,
    abstract: string,
    published: date,
    citations: int,
    influential_citations: int,
    upvotes_hf: int,              // from HF
    hn_points: int,               // from HN traction merge
    hn_num_comments: int,
    signal: float                 // last-computed signal_score
})
(:Author {orcid: string?, name: string})
(:Concept {name: string, family: string})    // "Linear Algebra" → "SVD" → "PCA"
(:Source {name: string})                     // "arxiv", "semantic_scholar", "huggingface", "hn"

// Relationships
(:Paper)-[:CITES]->     (:Paper)
(:Author)-[:AUTHORED]-> (:Paper)
(:Paper) -[:ABOUT]->    (:Concept)
(:Paper) -[:FROM]->     (:Source)

// Constraints
CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE;
CREATE INDEX paper_signal IF NOT EXISTS FOR (p:Paper) ON (p.signal);
CREATE INDEX paper_published IF NOT EXISTS FOR (p:Paper) ON (p.published);
```

**Implementation:** `apps/fastapi/infra/neo4j/` already exists (YCS uses
it). Reuse the same driver. Add `domains/rr/service.py::upsert_paper(p)`
that `MERGE`s by `arxiv_id` so cross-source ingest grafts onto the same
node — the killer architectural payoff.

#### 2.4.2 Qdrant collection

```python
# In apps/fastapi/domains/rr/service.py
COLLECTION = "radar_papers"
VECTOR_DIM = 2048   # matches embedding.model in values.yaml (llama-nemotron-embed-1b-v2)

await qdrant.recreate_collection(
    COLLECTION,
    vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
    optimizers_config=OptimizersConfigDiff(default_segment_number=2),
)
await qdrant.create_payload_index(COLLECTION, "arxiv_id", PayloadSchemaType.KEYWORD)
await qdrant.create_payload_index(COLLECTION, "signal", PayloadSchemaType.FLOAT)
await qdrant.create_payload_index(COLLECTION, "published", PayloadSchemaType.DATETIME)
```

**Embedding:** use the existing rotator's `embed_via_router_async`
(NIM `nvidia/llama-nemotron-embed-1b-v2`). One call per paper abstract
in the graph_build subagent.

#### 2.4.3 Postgres tables

```sql
-- apps/fastapi/domains/rr/service.py runs these at bootstrap
CREATE TABLE IF NOT EXISTS radar_scans (
    id              UUID PRIMARY KEY,
    profile_id      TEXT NOT NULL,
    status          TEXT NOT NULL,   -- pending · running · done · error · cancelled
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    total_candidates    INT NOT NULL DEFAULT 0,
    total_in_digest     INT NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS radar_findings (
    scan_id         UUID REFERENCES radar_scans(id) ON DELETE CASCADE,
    arxiv_id        TEXT NOT NULL,
    rank            INT NOT NULL,
    signal          DOUBLE PRECISION NOT NULL,
    digest_json     JSONB NOT NULL,
    PRIMARY KEY (scan_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS radar_seen (
    profile_id      TEXT NOT NULL,
    arxiv_id        TEXT NOT NULL,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS radar_profiles (
    id              TEXT PRIMARY KEY,
    interests       JSONB NOT NULL,    -- {"verticals": [...], "math_concepts": [...], ...}
    weights         JSONB NOT NULL,    -- SignalWeights overrides
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Implementation file:** `apps/fastapi/domains/rr/service.py`.
**Migration approach:** ad-hoc DDL at bootstrap (same pattern as DD/YCS).
No Alembic for the radar's tables.

#### 2.4.4 MinIO

Bucket = existing `coelhonexus`; prefix paths:
- `rr/scans/{scan_id}/digest.json` — final digest snapshot
- `rr/scans/{scan_id}/extractions/{arxiv_id}.json` — deep_read output
- `rr/scans/{scan_id}/pdfs/{arxiv_id}.pdf` — cached PDF (v2)

Use the existing `aioboto3` client from `infra/celery/storage_minio.py`.

---

### 2.5 Pure `domain.py` — signal_score · dedup · diff_vs_seen

**Location:** `apps/fastapi/domains/rr/domain.py` (PURE — no I/O).

```python
# domain.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta

from .entities import NormalizedPaper


@dataclass(frozen=True, slots=True)
class SignalWeights:
    """Per docs/CODE-CONVENTIONS.md §3 — frozen group of related tunables.
    Defaults tuned for LLMOps/agents/quant verticals; per-profile overrides
    live in radar_profiles.weights."""
    relevance:        float = 0.30   # cosine(profile, paper) via Qdrant
    recency:          float = 0.15   # half-life decay
    citation_velocity: float = 0.15  # OpenAlex/S2 per-day citation rate (when available)
    influential_ratio: float = 0.10  # S2 influential_cite / cite ratio (when available)
    vertical_fit:     float = 0.15   # cs.LG/AI overlap with profile.verticals
    cross_tier_buzz:  float = 0.10   # log1p(hn_points) + log1p(hf_upvotes)
    has_code:         float = 0.05   # Papers-with-Code presence (v2)


def signal_score(
    p: NormalizedPaper,
    profile_embedding: list[float],
    now: date,
    weights: SignalWeights,
) -> float:
    """Pure ranking score. Per CODE-CONVENTIONS.md §4 the body reads top-to-
    bottom: relevance · recency · velocity · influence · vertical · buzz · code."""
    rel = _cosine(profile_embedding, p.embedding) if p.embedding else 0.0
    rec = _recency_decay(p.published, now, half_life_days=30)
    vel = _velocity(p.citations, p.published, now)
    infl = (p.influential_citations / max(p.citations, 1)) if p.citations else 0.0
    fit = _vertical_fit(p.categories, p.profile_verticals)
    buzz = _log1p(p.hn_points) + _log1p(p.hf_upvotes)
    code = 1.0 if p.has_code else 0.0
    return (
        weights.relevance * rel +
        weights.recency * rec +
        weights.citation_velocity * vel +
        weights.influential_ratio * infl +
        weights.vertical_fit * fit +
        weights.cross_tier_buzz * buzz +
        weights.has_code * code
    )


def dedup_by_arxiv_id(items: list[NormalizedPaper]) -> list[NormalizedPaper]:
    """The cross-source merge — same arxiv_id seen via multiple sources
    collapses to one paper with merged signals (max points, max upvotes, …)."""
    by_id: dict[str, NormalizedPaper] = {}
    for it in items:
        if not it.arxiv_id:
            continue
        existing = by_id.get(it.arxiv_id)
        by_id[it.arxiv_id] = _merge(existing, it) if existing else it
    return list(by_id.values())


def diff_vs_seen(
    candidates: list[NormalizedPaper],
    seen_arxiv_ids: frozenset[str],
) -> tuple[list[NormalizedPaper], list[NormalizedPaper]]:
    """(new, returning). Powers the digest's 'New since last scan' section."""
    new = [c for c in candidates if c.arxiv_id and c.arxiv_id not in seen_arxiv_ids]
    returning = [c for c in candidates if c.arxiv_id and c.arxiv_id in seen_arxiv_ids]
    return new, returning


# Pure helpers (kept private)
def _cosine(a: list[float], b: list[float]) -> float: ...
def _recency_decay(published: date | None, now: date, half_life_days: int) -> float: ...
def _velocity(citations: int, published: date | None, now: date) -> float: ...
def _vertical_fit(categories: list[str], verticals: list[str]) -> float: ...
def _log1p(x: int) -> float: ...
def _merge(a: NormalizedPaper, b: NormalizedPaper) -> NormalizedPaper: ...
```

**Acceptance:** unit-tests against synthetic `NormalizedPaper` fixtures —
no event loop, no mocks. Reference shape: `apps/fastmcp/domains/rr/tools/arxiv/domain.py` smoke test.

---

### 2.6 API + Celery orchestration

#### 2.6.1 FastAPI router

```python
# apps/fastapi/api/v1/rr/scan/router.py
@router.post("/scan", response_model=ScanCreated)
async def create_scan(body: ScanRequest) -> ScanCreated:
    scan_id = uuid4()
    await persist_pending_scan(scan_id, body.profile_id)
    task = run_radar_scan.delay(str(scan_id), body.profile_id)
    return ScanCreated(scan_id=scan_id, task_id=task.id)

@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan(scan_id: UUID) -> ScanResult: ...

@router.get("/scan/{scan_id}/events")
async def scan_events(scan_id: UUID) -> StreamingResponse:
    """SSE — yields {phase, percent, message} events from Redis as the agent runs."""
    return StreamingResponse(_event_iter(scan_id), media_type="text/event-stream")
```

#### 2.6.2 Celery task

```python
# apps/fastapi/domains/rr/task.py
from infra.celery.app import celery_app

@celery_app.task(
    bind=True,
    queue=q("rr"),                      # new queue name; add to values.yaml celery.queues
    soft_time_limit=1800,
    time_limit=2100,
)
def run_radar_scan(self, scan_id: str, profile_id: str) -> dict:
    """Drives the DeepAgents graph; emits SSE events at each phase."""
    profile = load_profile(profile_id)
    agent = build_radar_agent()
    async_run = agent.ainvoke(
        {"profile": profile, "scan_id": scan_id, "started_at": ...},
        config={"configurable": {"thread_id": scan_id}},
    )
    result = asyncio.get_event_loop().run_until_complete(async_run)
    return persist_scan_result(scan_id, result)
```

**Queue wiring:** add `rr` to `values.yaml::celery.queues` (currently
`crawler,embedding,llm,planner,synth,default,ycs` → append `,rr`).

---

### 2.7 FastHTML UI — `apps/fasthtml/features/rr/`

```
apps/fasthtml/features/rr/
├── __init__.py
├── routes.py             # /research-radar route + SSE relay route
├── body.py               # main page body (topic profile editor · scan button · digest area)
└── components.py         # paper_card · progress_bar · concept_chip

apps/fasthtml/static/js/rr/
├── scan.js               # POST /scan + SSE subscription + progress UI
└── digest.js             # render ranked digest cards
```

**Page layout:**
```
┌─────────────────────────────────────────────┐
│  Research Radar          [Profile ▾] [Scan]│
├─────────────────────────────────────────────┤
│  Interest profile:                          │
│  [LLMOps] [Agents] [Quant] [Math: linalg]   │
├─────────────────────────────────────────────┤
│  Last scan:  2026-06-11 03:42  (12 items)   │
│  Progress:   ▓▓▓▓▓▓░░░░  62% · synthesis    │
├─────────────────────────────────────────────┤
│  ★ NEW SINCE LAST SCAN (3)                  │
│   ─────────────────────────                 │
│   [signal 0.91 · arXiv 2406…]                │
│   "Constrained Decoding without Logits"     │
│   problem → tool-call schema violations     │
│   method  → speculative validate+retry      │
│   …                                         │
└─────────────────────────────────────────────┘
```

**Defer graph viz to v2.** Cytoscape integration adds significant frontend
weight; the digest cards + concept chips are enough for v1.

---

### 2.8 Observability — LangFuse dashboard

LangFuse already receives spans (telemetry middleware). Add ONE saved
dashboard with these panels:

1. Per-source latency (`mcp.tool.{name}` p50/p95/p99)
2. Per-source error rate (`is_error=true` percentage)
3. Per-scan cost (tokens × $/M) — aggregated by `scan_id` resource attribute
4. Subagent fan-out depth (count of `rr.subagent.*` spans per scan)
5. Cross-source dedup hit rate (custom metric — emit from `domain.dedup_by_arxiv_id`)

**LGTM/Grafana fan-in:** defer to v2 — the data IS landing in Tempo, just
no pinned dashboard yet.

---

### 2.9 Portfolio polish

| Item | Location | Acceptance |
|---|---|---|
| README section for RR | `README.md` | Architecture diagram (lifted from design doc §2.3) + screenshot of digest |
| Technical Decisions writeup | `docs/RESEARCH-RADAR-TECHNICAL-DECISIONS.md` | The "why source-specific Paper shapes, not unified" call · the "why HN dedup via URL extraction" call · the "why Settings UI BYOK reuses LLM store" call |
| 5-min Loom demo | (external) | Triggers scan → SSE progress → digest renders → cross-source dedup visible in graph |

---

## 3. v2 deferrals (after MVP loop closes)

| Item | Trigger |
|---|---|
| Tool: `openalex` | After confirming personal email is accepted; large citation graph value |
| Tool: `papers_with_code` revival | Once their API stabilizes (currently immature post-revival) |
| Tool: RSS / blog feeds | When the news-tier appetite outgrows HN |
| Tool: MIT OCW / open textbooks | For the math-restudy mode (separate sub-tier) |
| Tool: `browser_use` for JS-rendered pages | When deep_read can't extract from a paper's project page |
| Elasticsearch full-text | When Neo4j Cypher + Qdrant cosine miss queries the agent needs |
| Celery beat scheduled scans + Novu notifications | When manual triggers feel like friction |
| DeepAgents context middleware (compress / offload / cache) | When deep_read fan-out > 50 papers/scan |
| Sampling / Elicitation FastMCP primitives | When an agent flow genuinely needs user prompts mid-run |
| Human-approval gate before report commit | When radar moves from single-user to multi-tenant SaaS |
| LGTM/Grafana fan-in (panel-embed) | When LangFuse views aren't enough |
| Retrofit telemetry middleware into DD + YCS | When DD/YCS observability becomes a recruiter-demo priority |
| Tool: `github` via `create_proxy` | Independent of agent — can land any time |

---

## 4. Open decisions (resolve before §2.3 build)

1. **Source-specific Paper vs unified `NormalizedPaper`?** Each tool returns
   its own `Paper`/`Hit` shape; the agent normalizes at ingest. → `entities.py`
   in `domains/rr/` holds `NormalizedPaper`; subagents map source-specific →
   normalized at `graph_build` step. **Decided.**

2. **One subagent per source or one subagent over all sources?** One per
   source — gives DeepAgents the isolated-context payoff and parallel
   fan-out. **Decided.**

3. **Postgres queues — share fastapi's pool or separate?** Share the
   existing pool (DD/YCS already do). **Decided.**

4. **Profile storage — Postgres `radar_profiles` table or MinIO JSON?**
   Postgres — queryable, transactionally consistent with `radar_scans`.
   **Decided.**

5. **Scan kickoff — Celery now or BackgroundTasks?** Celery — same long-
   running shape DD/YCS use; SSE relays via Redis pub/sub. **Decided.**

6. **DeepAgents version pin?** Pin to `>=0.5,<1.0` to lock async
   subagent + multimodal-FS features. **To resolve when adding to
   `pyproject.toml`.**

---

## 5. The first hour of tomorrow

1. **`mkdir apps/fastapi/domains/rr/agent/`** + create the 8 skeleton files
   listed in §2.3.1 (empty stubs except `__init__.py`).
2. **Add `deepagents>=0.5,<1.0`** and `langgraph-checkpoint-postgres` (if not
   already present) to `apps/fastapi/pyproject.toml`.
3. **Build `agent/mcp_client.py`** — a singleton `fastmcp.Client` returning
   LangChain `BaseTool` adapters for each remote MCP tool.
4. **Build `agent/graph.py`** — minimum `create_deep_agent(...)` call with
   ONE subagent (`discovery_arxiv`) that calls the arxiv tool via the
   adapter from step 3.
5. **Smoke-test from a Python REPL inside the fastapi pod:**
   ```python
   from domains.rr.agent.graph import build_radar_agent
   agent = build_radar_agent()
   result = await agent.ainvoke({"messages": [{"role": "user",
                                  "content": "find recent deep agents papers"}]})
   ```
   → if the trace shows the discovery subagent calling `arxiv_search` and
   returning structured papers, **the agent skeleton works** and §2.3.2 →
   §2.7 fall in line.

That's the single biggest forward step available. Everything else (stores,
API, UI) follows naturally from what the agent's output shape ends up
being.

---

## Sources

- [`RESEARCH-RADAR-DESIGN-2026-06-10.md`](./RESEARCH-RADAR-DESIGN-2026-06-10.md)
- [DeepAgents docs](https://docs.langchain.com/oss/python/deepagents/overview)
- [LangGraph PG checkpointer](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres)
- [FastMCP — Resources + Prompts + Composition + Proxy](https://gofastmcp.com/)
- [agents-radar reference pattern (signal-scoring loop)](https://github.com/duanyytop/agents-radar)
