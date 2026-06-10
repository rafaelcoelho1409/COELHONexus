# Research Radar — Design (2026-06-10)

**Status:** DESIGN-LOCKED, baseline scaffolded, tools/agent pre-implementation.
**Scope:** the 3rd top-level feature of COELHO Nexus (replaces the "Coming Soon" tile, now `/research-radar`). One DeepAgents app that continuously scans free academic + news sources, builds a citation/concept graph, and emits a ranked "implementable ideas" digest aimed at the operator's actual money/learning verticals (LLMOps · agents · quant finance · math restudy). $0 hard-cost; runs entirely on the existing free-tier LLM rotator.

**Cross-references:**
- [`CODE-ORGANIZATION-SOTA-2026-05-20.md`](./CODE-ORGANIZATION-SOTA-2026-05-20.md) — `apps/` + `domains/` + `features/` layout this feature follows.
- [`CODE-CONVENTIONS.md`](./CODE-CONVENTIONS.md) — per-module file conventions (`tool.py`/`service.py`/`domain.py`/`schemas.py`/`params.py`).
- [`SENTINEL-MCP-SOTA-2026-06-09.md`](./SENTINEL-MCP-SOTA-2026-06-09.md) — superseded as a standalone feature; its **FastMCP governance + LangFuse/OTel observability core lives on as middleware *under* Research Radar**.

---

## 1. The decision: radar, not research assistant

Two paths were considered:

| Option | What it'd be | Verdict |
|---|---|---|
| **A. Radar** — scan the frontier, rank emerging implementable ideas, surface them continuously | Personal-intelligence + learning tool with a sharp opinion ("here are 5 things worth implementing this week, and the math behind them") | **Picked.** |
| B. Research assistant — answer "summarize the literature on X" | Saturated namespace (Elicit · Consensus · Undermind · SciSpace · ResearchRabbit). Hard to differentiate. | Rejected. |

The framing is the moat. Deep Research / news-feed clones are oversaturated in 2026; **"signal radar with implementability + money/study angle"** is a defensible vertical *inside* the same architecture.

Aligned with the operator's revealed direction (study plan = AI/LLMOps + quant finance, ≈zero cyber). The earlier passive-OSINT security tool stays as a private back-pocket asset; this is the public/portfolio build.

---

## 2. Architecture — three surfaces

### 2.1 Three peer apps (matches the project's `apps/<svc>/` rule)

```
COELHO Nexus
├── apps/fastapi/      API + Celery + agent orchestrator
│   └── domains/rr/    Research Radar business logic (NEW)
├── apps/fasthtml/     Server-rendered BFF UI
│   └── features/rr/   Research Radar page (NEW)
└── apps/fastmcp/      MCP tool surface (NEW peer app — built 2026-06-10)
    └── tools/         arxiv · openalex · …  +  middleware/
```

Naming set: **fastapi · fasthtml · fastmcp.** All three are Starlette/ASGI uvicorn targets; only `fasthtml` is public-ingress, the other two stay ClusterIP.

### 2.2 End-to-end flow

```
browser ──HTMX──▶ FastHTML /research-radar
                       │
                       │ server-side httpx (BFF) + internal credential
                       ▼
                  FastAPI  POST /api/v1/rr/scan
                       │
                       ▼
                  Celery  run_radar_scan(profile)
                       │
                       ▼
              ┌────────────────────────────────────────────┐
              │ DeepAgents ORCHESTRATOR (LangGraph)         │
              │   write_todos plan over interests profile    │
              └───────────────┬────────────────────────────┘
                              │ spawns async subagents
                              ▼
                  FastMCP CLIENT (fastmcp.Client over Streamable-HTTP)
                              │
                              ▼   (Mcp-Session-Id, JSON-RPC 2.0)
                  apps/fastmcp  http://coelhonexus-fastmcp:8000/mcp/
                       ├─ middleware: auth · rate-limit · OTel span · LangFuse trace
                       └─ tools: arxiv · openalex · (s2 · papers-with-code · rss …)
                              │
                              ▼
                  Stores: Neo4j (graph) · Qdrant (vectors) · MinIO (PDFs) ·
                          Postgres (LangGraph ckpt + scans/findings/seen)
                              │
                              ▼ SSE progress (Redis)
                  back through FastAPI → FastHTML → browser
```

LLM reasoning at every step routes through the existing **free-tier rotator** (cheap models triage/extract, strong models synthesize) — no paid SaaS, no in-cluster inference.

---

## 3. The DeepAgents topology

**Pattern:** orchestrator + isolated-context worker subagents coordinating through a **blackboard** (the shared stores), not by passing big context to each other. The execution tree is **emergent** from `write_todos`, not statically wired.

```
ORCHESTRATOR (write_todos → plan)
  ├─ Discovery subagents     (per source) → candidate papers       [FastMCP tools]
  ├─ Triage / Rank           → signal_score → top-N shortlist       [Qdrant + OpenAlex]
  ├─ Deep-read subagents     (async, top-N) → extract {problem ·
  │     method · math used · result · novelty · implementability ·
  │     how-to-build · money/study angle}                          [virtual-FS offload]
  ├─ Graph-build             → upsert papers / cites / concepts    [Neo4j + Qdrant]
  ├─ Synthesis (GraphRAG)    → emerging clusters · cross-paper themes
  └─ Report                  → ranked digest · "new since last scan"
                                                               [Postgres checkpoint]
```

DeepAgents features exercised: **planning** (`write_todos`) · **isolated subagents** (per source / per paper) · **async fan-out** · **virtual filesystem** (offload big tool outputs out of context) · **Postgres checkpoint** (resume long scans, diff vs last run) · **context middleware** (compress / cache).

**Hybrid where it matters:** where ordering must be reproducible (security loves audit trails; here: deep-read → graph-build → synthesis), drop to a **hand-drawn LangGraph sub-flow** and expose it to the orchestrator as a single tool. Adaptive planning + a couple of deterministic sub-graphs — the senior judgment call.

---

## 4. FastMCP tool surface

### 4.1 Source tiers — three kinds, same wire

| Tier | Role | Sources (v1 → v2) |
|---|---|---|
| **Academic core** (leading) | "what's emerging at the frontier" months before it's news | arXiv · OpenAlex → +Semantic Scholar · Papers with Code |
| **News timely** (lagging-but-signal) | "what's gaining traction *now*" — community buzz, implementations | RSS · HN · /r/MachineLearning · GitHub trending · selected blogs |
| **Math / study** (v2) | "the math behind the trend" — refresher context | arXiv math.* · MIT OCW · open textbooks (Bengio DL, Goodfellow) |

**The "+1" insight that justifies all three:** the highest-signal moments cross tiers — "this paper from 3 months ago is blowing up on HN with a working repo." Cross-tier correlation is what makes RR sharper than any single source. (See §10 for math-mode specifics.)

### 4.2 Per-tool layout (conventions-compliant)

Each tool is a directory under `apps/fastmcp/tools/<name>/` following [`CODE-CONVENTIONS.md`](./CODE-CONVENTIONS.md):

```
apps/fastmcp/tools/arxiv/
├── __init__.py
├── tool.py        # @mcp.tool — thin: validate → service → return
├── service.py     # async httpx I/O (export.arxiv.org/api/query)
├── domain.py      # PURE: parse Atom XML → list[Paper]
├── schemas.py     # Pydantic: SearchInput · Paper
└── params.py      # BASE_URL · PAGE_SIZE_MAX · USER_AGENT · timeout_s
```

Pattern rationale: thin tool boundary → fast LLM-schema generation; pure `domain.py` → trivially unit-testable (no httpx mock); `service.py` orchestrates I/O. Every subsequent tool copies the shape.

### 4.3 Middleware (the Sentinel-substrate reframe)

`apps/fastmcp/middleware/` holds cross-cutting `on_call_tool` hooks:

| Middleware | Job |
|---|---|
| `telemetry.py` | OTel span (W3C trace context) + LangFuse trace per call. No FastMCP OTel auto-instrumentation as of 2026-06 — this middleware IS the instrumentation point. |
| `ratelimit.py` | Per-source ToS compliance (arXiv: 1 req/3s; OpenAlex: 100k/day) |
| `auth.py` (v2) | JWT verification — gated when the server is exposed beyond ClusterIP. |

These middlewares are the **Sentinel core** living *under* the agent — no separate gateway page/product needed.

---

## 5. Data model

### 5.1 Neo4j — citation / concept / author graph (the GraphRAG substrate)

```
(Paper {id, title, abstract, date, source, citations, velocity, signal})
(Author {orcid?, name})
(Concept {name, family})    # "Linear Algebra" → "SVD" → "PCA"
(Source {name})             # "arXiv", "OpenAlex", "HN", …

(Paper) -[:CITES]->     (Paper)
(Author)-[:AUTHORED]->  (Paper)
(Paper) -[:ABOUT]->     (Concept)
(Paper) -[:FROM]->      (Source)
```

GraphRAG queries: dense recent-high-velocity subgraphs surface "emerging clusters"; concept-prerequisite chains power math-restudy mode.

### 5.2 Qdrant — semantic memory

Collection `radar_papers`: abstract embedding (via rotator) + signal payload. Use cases:
- Relevance: `cosine(profile, paper)` → fit score.
- Dedup: near-duplicate detection across sources.
- "more-like-this" expansion during deep-read.

### 5.3 Postgres — durable state + diff

- LangGraph checkpoints (resume long scans).
- `radar_scans` — one row per scan run (status, started_at, profile_id, totals).
- `radar_findings` — top-N papers per scan with extracted summaries + signal score.
- `radar_seen` — `(profile_id, paper_id)` set powering the "**new since last scan**" diff.
- `radar_profiles` — interest profiles (LLMOps · quant · math.restudy · …).

### 5.4 MinIO — object store

PDFs, deep-read extractions (JSON), report snapshots — large blobs out of Postgres.

### 5.5 Elasticsearch — v2 full-text

Keyword + boolean over all ingested text. v2 only — Neo4j + Qdrant cover v1.

---

## 6. Signal score (pure, `domain.py`)

The "radar" — papers/items are ranked, not just listed.

```
signal = w_rel · relevance         # cosine(profile, paper) via Qdrant
       + w_rec · recency           # half-life decay since publication
       + w_vel · citation_velocity # OpenAlex per-day citation rate
       + w_fit · vertical_fit      # match to interests (LLMOps/quant/math)
       + w_impl · has_code         # Papers-with-Code presence (v2)
       + w_buzz · cross_tier_buzz  # HN+GitHub mentions (v2)
```

Weights live in `domains/rr/params.py` as a `frozen-dataclass` `SignalWeights` (per [`CODE-CONVENTIONS.md`](./CODE-CONVENTIONS.md) §3). The function is pure → property-testable; the weights are tunable per-profile (math-restudy weights `recency` lower, `concept_match` higher).

---

## 7. Observability — cross-cutting from day 1

OTel = the standard/transport; LangFuse = the LLM-native destination.

- **FastMCP middleware** emits an OTel span + LangFuse trace per tool call.
- **Rotator wrapper** (`domains/llm/`) emits the same for every LLM call.
- **DeepAgents observability spans** wrap each subagent → nested trace tree showing plan → fan-out → triage → deep-read → synthesis.

One LangFuse dashboard surfaces: per-source latency · per-tool error rate · per-stage cost · prompt/response chains · evals. LGTM/Grafana fan-in is a v2 polish.

**Designed cross-cutting, implemented EASM-first:** the telemetry layer is built generic enough to retrofit DD/YCS post-window (one-line import per call-site) — same architecture, staged implementation.

---

## 8. Code layout

```
apps/fastmcp/                              ── NEW peer app (baseline shipped 2026-06-10)
├── server.py                              root FastMCP() — mounts each domain's sub-server
├── pyproject.toml · Dockerfile.fastmcp · entrypoint.sh · .dockerignore
├── shared/
│   └── middleware/                        cross-cutting (applies to ALL features)
│       ├── telemetry.py                   on_call_tool → OTel + LangFuse
│       └── ratelimit.py                   per-source ToS
└── domains/                               ── mirrors apps/fastapi/domains/ ──
    └── rr/
        ├── server.py                      rr_mcp = FastMCP("rr") — mounted at /rr
        ├── tools/                         BUILD or WRAP (FastMCP create_proxy)
        │   ├── arxiv/    tool · service · domain · schemas · params  (BUILD)
        │   ├── semantic_scholar/          (WRAP via create_proxy — Proxy primitive)
        │   └── hn/       (BUILD — Algolia API)
        ├── resources/                     radar://latest_digest, radar://concept/{name}
        ├── prompts/                       /digest_today user-slash-command
        └── middleware/                    rr-specific only (rare)

apps/fastapi/domains/rr/                   ── NEW (consumes fastmcp as MCP client)
├── agent/
│   ├── graph.py         build_radar_agent() — orchestrator + subagent roster
│   ├── state.py
│   ├── mcp_client.py    fastmcp.Client → http://coelhonexus-fastmcp:8000/mcp/
│   ├── subagents/
│   │   ├── discovery/     prompts · service · schemas
│   │   ├── triage/        (pure ranking via domain.signal_score)
│   │   ├── deep_read/     prompts · service · schemas (+ domain for extraction)
│   │   ├── synthesis/     prompts · service (GraphRAG over Neo4j)
│   │   └── report/        prompts · service
│   └── observability/spans.py
├── domain.py             PURE: signal_score · dedup · diff_vs_seen
├── service.py            I/O: Neo4j · Qdrant · Postgres via infra/ + rotator
├── schemas.py · entities.py · keys.py · params.py · errors.py
└── task.py               Celery run_radar_scan → drives agent, emits SSE

apps/fastapi/api/v1/rr.py                  thin router: POST /scan · GET /scan/{id} · SSE
apps/fasthtml/features/rr/                 body.py · components.py (+ static/js/rr/*)
```

---

## 9. K8s / deploy (already wired 2026-06-10)

- **Helm**: `k8s/helm/templates/fastmcp/{deployment,service,configmap}.yaml` — mirrors the `fasthtml` macro pattern (`coelhonexus.DeploymentSettings`, `coelhonexus.ServicePortsSettings`, etc.).
- **Values**: `fastmcp:` block — ClusterIP:8000 (internal-only), `/health` startup/liveness/readiness probes, fasthtml-sized resources (256→512Mi, 100m→500m CPU).
- **Skaffold**: `localhost:5001/coelhonexus-fastmcp` artifact, `fastmcp.image` setValueTemplate, port-forward.
- **Port-forward pairing** (last digit aligns dev ↔ prod):

  | Service  | Dev (Skaffold) | Prod (ArgoCD) |
  |---|---|---|
  | fastapi  | 23020 | 23000 |
  | flower   | 23022 | 23002 |
  | fasthtml | 23023 | 23003 |
  | **fastmcp**  | **23024** | **23004** |

- **Health check**: `curl http://localhost:23024/health` → `{"status":"ok","server":"coelhonexus-mcp"}`.
- **MCP endpoint** at `/mcp/` — exercised via the **FastMCP Python client** (not curl-friendly; JSON-RPC 2.0 over Streamable-HTTP, requires `initialize → tools/list` handshake).

---

## 10. Math-restudy mode

The operator holds a Math BSc (UFPR) and is re-studying math to deepen AI/ML — see [`MEMORY/user_math_restudy_goal_2026_06_10.md`]. Honest scope:

✅ **Excellent for:**
- *Refresher* on concepts as they're **applied** in modern ML (eigendecomposition, KL divergence, Jacobians recurring across papers).
- Grounding restudy in **why** the math matters today.
- Building a Neo4j **concept-prerequisite graph**: linear algebra → eigendecomposition → PCA → diffusion model X.

❌ **Bad for:**
- *First-principles* learning (papers assume fluency, notation varies, no pedagogical scaffolding).

**Practical mode:** a "math.restudy" interest profile uses `cs.LG` + `math.*` arXiv categories, weights `concept_match` over `recency`, and the deep-read subagent extracts the *math used* field. Weekly digest = "this week's emerging idea · the math concept it leans on · which textbook chapter would refresh it." v2 adds an MIT-OCW / open-textbook tool to make that last column source-grounded.

---

## 11. The radar's headline output

A weekly (or on-demand) ranked digest per profile:

```
RESEARCH RADAR — 2026-06-15  (profile: LLMOps + agents + math)

★ NEW THIS WEEK (5 items, signal ≥ 0.75)

1. [signal 0.91 · arXiv 2606.04123 · 14 cites/day]
   "Constrained Decoding Without Logit Access for Open Tool-Use Agents"
   problem      → tool-call schema violations on open-weight serving APIs
   method       → speculative validate-then-emit w/ KL-bounded retry
   math used    → KL divergence · token-level log-prob ranking
   how to build → drop-in around LiteLLM completion; 2 hooks
   money angle  → cuts agent-tool error rate ~30% → fewer wasted LLM calls
   →  HN traction: 412 pts, 89 comments · github.com/.../decode-v2 (1.2k★)

2. [signal 0.88 · OpenAlex W4399… · 9 cites/day]
   "Async Subagents for Long-Horizon Web Research" …

▸ EMERGING CLUSTER  GraphRAG over recent agent-eval literature (7 papers,
  3 new this week). Cross-paper theme: "judge ensembles strictly dominate
  single-LLM-judge once you control for budget."  See concept page →

⌖ MATH THREAD  4 of this week's top 10 lean on KL divergence + softmax-
  temperature interactions. Refresher: Goodfellow §3.13 (Information Theory).
```

That output, in one glance, is what makes RR a *radar* and not a literature-review tool.

---

## 12. Build sequence

| Step | Status |
|---|---|
| FastHTML 3rd tile renamed → Research Radar (`/research-radar`) | ✅ done |
| `apps/fastmcp/` baseline (server · uv pyproject · Dockerfile · entrypoint · `/health` · `ping` tool) | ✅ done |
| K8s/Helm wiring (deployment · service · configmap · values) | ✅ done |
| Skaffold artifact + port-forward + image template | ✅ done |
| `tools/arxiv/` (tool · service · domain · schemas · params) | ▶ NEXT |
| `tools/openalex/` (same shape) | ⏭ then |
| `middleware/{telemetry, ratelimit}` | ⏭ then |
| `domains/rr/agent/` skeleton (orchestrator + 1 subagent end-to-end) | ⏭ then |
| Neo4j schema + Qdrant collection + Postgres tables | ⏭ |
| Celery task + FastAPI router + SSE + FastHTML page | ⏭ |
| LangFuse dashboard + README + 5-min demo | ⏭ |

### Deferred to v2 (after MVP loop closes)
- Semantic Scholar · Papers with Code · RSS · HN · GitHub trending tools
- MIT OCW + open-textbook tool (math-restudy upgrade)
- Browser Use (Chromium runtime — heaviest infra, defer)
- Elasticsearch full-text tier
- Scheduled scans (Celery beat) + Novu notifications
- Cytoscape graph visualization on the FastHTML page
- LGTM/Grafana fan-in
- Retrofit telemetry middleware into DD + YCS

---

## 13. Sources

- [FastMCP docs](https://gofastmcp.com/) · [HTTP Deployment](https://gofastmcp.com/deployment/http)
- [DeepAgents docs](https://docs.langchain.com/oss/python/deepagents/overview) · [deepagents (GitHub)](https://github.com/langchain-ai/deepagents)
- [arXiv API basics](https://info.arxiv.org/help/api/index.html)
- [OpenAlex API docs](https://docs.openalex.org/)
- [LangFuse OTel integration](https://langfuse.com/docs/opentelemetry/get-started)
- [OpenTelemetry for AI Agents / MCP (MintMCP)](https://www.mintmcp.com/blog/opentelemetry-ai-agents)
