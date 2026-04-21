# YouTube Content Search — ANALYST Mode

> DeepAgents-powered 4th mode for the Adaptive RAG graph. Handles genuinely exploratory research questions that the static DEEP decomposition can't: argument trees, temporal evolution, contradiction deepening, cross-session memory.
>
> **Status:** Design complete, implementation DEFERRED until prerequisites ship.
> **Prerequisites (in order):** Knowledge Distiller → Textbook-to-Code Agent → Reddit Ingest + Analyst Mode → then this.
> **Why this order:** Reddit Analyst forces the DeepAgents analyst pattern on fresh data first; YouTube reuses it with a proven template and a free 41k-node test bed (existing corpus).

---

## 1. TL;DR

Current Adaptive RAG has three modes:

| Mode | Shape | Latency | Best for |
|---|---|---|---|
| FAST | direct LLM, no retrieval | <2s | trivia, definitions |
| STANDARD | retrieve → grade → generate → check | 15–60s | factual Qs needing citations |
| DEEP | planner → N parallel `Send()` → synthesize → critic | 30–120s | decomposable analytical Qs (pattern finding, contradictions across a fixed set of angles) |

All three are **static graphs** — the execution path is fixed at compile time.

**ANALYST** is the new 4th mode:

| Mode | Shape | Latency | Best for |
|---|---|---|---|
| ANALYST | DeepAgents harness: `write_todos` + `task()` subagents + filesystem + SKILL.md + user's AGENTS.md | **3–15 min** | research-grade questions where the next investigation depends on what the last one revealed |

Trade: longer runs and higher cost for genuinely adaptive reasoning.

---

## 2. The gap ANALYST fills

DEEP mode decomposes a question into 3–8 sub-questions **once**, runs them in parallel, then merges. It fails when:

| Query shape | Why DEEP fails | Example |
|---|---|---|
| **Recursive decomposition** (next question depends on first answer) | Plan is fixed at dispatch | *"Map the argument tree on Dubai as a crypto base"* — you can only map the tree if you let sub-answers spawn new questions |
| **Temporal sequencing** | No way to time-window per sub-task | *"How did creator sentiment on Portugal NHR evolve 2022 → 2025?"* |
| **Cross-referencing claims vs outcomes** | Requires pulling separate corpus windows | *"Which creators predicted X correctly?"* |
| **Evidence weighing with feedback** | No notion of source credibility differentials | *"Find the most trustworthy take on topic Y"* |
| **Open-ended dossiers** | No accumulating research state | *"Build an intelligence profile on creator Z: core claims, bias indicators, contradictions"* |
| **Interactive refinement** | One-shot interaction model | *"Research this and ask me questions as you go"* |

These share one property: **the plan emerges from findings**, not from the initial question.

---

## 3. When to use ANALYST vs other modes

Decision table for the auto-classifier (and for `force_mode`):

| Signal in question | Route to |
|---|---|
| Question answerable from general knowledge | FAST |
| Question asks about specific transcript content, single angle | STANDARD |
| Question asks to compare/pattern-find across a small fixed set of angles | DEEP |
| Question contains: *"map argument tree"*, *"how did X evolve"*, *"research"*, *"investigate"*, *"build dossier"*, *"find contradictions and deepen"* | ANALYST |
| User explicitly says *"take your time"* or *"I'll come back later"* | ANALYST |

ANALYST is always opt-in via `force_mode: "analyst"` in v1. Auto-routing decisions are deferred until post-launch tuning.

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  POST /api/v1/youtube/agents/search  force_mode="analyst"           │
│  body: {question, thread_id, channel_ids?, analyst_budget_seconds?} │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Analyst Handler (FastAPI)                                           │
│  1. Load user AGENTS.md (per-user profile) from PostgreSQL           │
│  2. Load thread conversation history                                 │
│  3. Create session filesystem: studies/analyst/<thread_id>/<ts>/     │
│  4. Enqueue Celery task — analyst runs as background job             │
│     (too long for sync; SSE streams progress to the client)          │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Celery Worker — Analyst Task                                        │
│                                                                      │
│  agent = create_deep_agent(                                          │
│      tools=[                                                         │
│          youtube_smart_retriever,     # wraps services/youtube       │
│          neo4j_cypher_query,                                         │
│          get_creator_profile,                                        │
│          temporal_filter_videos,                                     │
│          list_channels,                                              │
│      ],                                                              │
│      subagents=[contradiction_finder, evolution_tracer,              │
│                 evidence_weigher, creator_profiler],                 │
│      system_prompt=ANALYST_PROMPT + AGENTS.md(user),                 │
│      backend=FilesystemBackend(                                      │
│          root=studies/analyst/<thread_id>/<ts>/),                    │
│      model=llm_with_fallbacks,                                       │
│  )                                                                   │
│                                                                      │
│  for event in agent.astream({messages: [{role, question}]},          │
│                             stream_mode="updates"):                  │
│      publish(event)  # SSE via Redis pub/sub                         │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Session output persists on disk:                                    │
│  studies/analyst/<thread_id>/<timestamp>/                            │
│    ├── todos.json            # accumulated plan                      │
│    ├── findings.md           # research notes the agent wrote        │
│    ├── contradictions.md     # disagreements surfaced                │
│    ├── creators-profiled.md  # dossiers built                        │
│    ├── conclusion.md         # final synthesis                       │
│    └── transcript.jsonl      # full event stream for audit           │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Client (SSE consumer)                                               │
│  Renders in real-time:                                               │
│  - Todo list updates                                                 │
│  - Research note stream                                              │
│  - Subagent spawn/finish events                                      │
│  - Citations as they accumulate                                      │
│  - Final conclusion                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. DeepAgents primitives mapped

| Primitive | Role in ANALYST |
|---|---|
| **`write_todos`** | Initial research plan written after the first LLM turn; dynamically appended to as findings surface. Visible to user via SSE (research-todo UI). |
| **`task("subagent_name", ...)`** | Spawns a fresh-context subagent for a specialized sub-investigation. Three canonical subagents defined below. |
| **Filesystem backend** | Research notes persist on disk under `studies/analyst/<thread_id>/<timestamp>/`. Agent reads/writes `findings.md`, `contradictions.md`, etc. Client can fetch these files post-run. |
| **SKILL.md files** | Reusable analytical patterns: `detect-contradictions.md`, `trace-evolution.md`, `rank-evidence-quality.md`, `build-creator-dossier.md`. Agent retrieves via built-in skill lookup. |
| **AGENTS.md per-user** | Cross-session memory: user's interests, dismissed creators, preferred depth. Stored in PG, loaded at session start, updated on session end. |
| **Context quarantine** | Each subagent sees only its task + relevant skill, not the main agent's full trajectory. Prevents token bloat on long investigations. |

---

## 6. Tools

Bound to the agent at `create_deep_agent(tools=[...])`:

| Tool | Signature | Wraps | Purpose |
|---|---|---|---|
| `youtube_smart_retriever` | `(query: str, channel_ids: list[str] = None, top_k: int = 10) -> list[Doc]` | `services.youtube.retriever.SmartRetriever.retrieve` | Hybrid dense+sparse+graph retrieval over ingested corpus |
| `neo4j_cypher_query` | `(cypher: str) -> list[dict]` | `app.state.neo4j_graph.query` | Read-only Cypher for graph traversal (whitelist SELECT-only patterns at validation time) |
| `get_creator_profile` | `(channel_id: str) -> dict` | New service — aggregates all videos + top entities + sentiment distribution | Creator-level view for dossiers |
| `temporal_filter_videos` | `(channel_id: str, start: date, end: date) -> list[video_id]` | ES date-range query | Time-window scoping for evolution analyses |
| `list_channels` | `() -> list[{channel_id, name, video_count}]` | ES aggregation | Discover available sources |
| `write_findings` | `(section: str, content: str)` | Filesystem write to `findings.md` | Accumulate research notes |
| `read_findings` | `(section: str = None) -> str` | Filesystem read | Check prior notes during synthesis |

Read-only by design. No tool writes to ES/Qdrant/Neo4j — the analyst doesn't mutate the ingested corpus.

---

## 7. Subagents

Defined as DeepAgents `Subagent` specs. Each has isolated context and a named skill.

### 7.1 `contradiction_finder`

```
description: "Deep-dive a disagreement between two or more creators' claims"
skill_file: skills/detect-contradictions.md
tools: [youtube_smart_retriever, neo4j_cypher_query, write_findings]
input: {claim_a: {text, source_video_id}, claim_b: {text, source_video_id}, topic: str}
output: {
  severity: "semantic" | "factual" | "value_judgment",
  evidence_a: list[quote + video_id],
  evidence_b: list[quote + video_id],
  reconciliation: str | null,  # if the disagreement can be reconciled, how
  trust_signals: {a: float, b: float},  # source credibility 0-1
}
```

### 7.2 `evolution_tracer`

```
description: "Trace how views on a topic evolved over a time window"
skill_file: skills/trace-evolution.md
tools: [youtube_smart_retriever, temporal_filter_videos, write_findings]
input: {topic: str, start: date, end: date, channel_ids?: list[str]}
output: {
  timeline: list[{period: str, dominant_stance: str, supporting_quotes: list[str], shift_drivers: list[str]}],
  inflection_points: list[{date, event, impact}],
  current_consensus: str | null,
}
```

### 7.3 `evidence_weigher`

```
description: "Rank the credibility of multiple sources on a specific claim"
skill_file: skills/rank-evidence-quality.md
tools: [get_creator_profile, neo4j_cypher_query, write_findings]
input: {claim: str, candidate_sources: list[{video_id, quote}]}
output: {
  ranked: list[{source, credibility_score, reasoning}],
  methodology: "track_record" | "specificity" | "corroboration" | "mixed",
}
```

### 7.4 `creator_profiler`

```
description: "Build a full dossier on one creator"
skill_file: skills/build-creator-dossier.md
tools: [get_creator_profile, youtube_smart_retriever, neo4j_cypher_query, write_findings]
input: {channel_id: str, focus_topics?: list[str]}
output: {
  core_claims: list[str],
  contradictions_self: list[str],   # creator disagreeing with themselves over time
  bias_indicators: list[str],
  evidence_quality: float,
  niche_expertise: list[str],
}
```

---

## 8. SKILL.md sketches

Each skill is a markdown file loaded by DeepAgents at startup and retrieved on demand by subagents.

### `skills/detect-contradictions.md` (sketch)

```markdown
# Skill: Detect Contradictions

## When to use
The `contradiction_finder` subagent loads this skill when comparing two or more claims.

## Classification
Contradiction types:
- **Semantic** — claims look opposite but reconcile under clarification
- **Factual** — objectively verifiable; one is wrong
- **Value judgment** — not a real disagreement, preferences differ

## Process
1. Restate each claim in its strongest form
2. Identify the logical quantifier (always, sometimes, never)
3. Check if quantifiers conflict or complement
4. Look for hidden assumptions (context, audience, time period)
5. Gather direct transcript quotes for both sides
6. Classify severity
7. If possible, suggest a reconciliation

## Output format
Strict JSON matching the subagent's output schema.
```

Other skills follow the same structure: **when to use / classification / process / output**.

---

## 9. AGENTS.md — per-user memory

Stored in PostgreSQL, loaded on session start, updated on session end.

```markdown
# User: <user_id>

## Interests (inferred from query history)
- Dubai relocation + taxation
- Caribbean CBI programs
- Crypto-friendly jurisdictions

## Dismissed topics
- NFT projects (user flagged as noise in 3 prior sessions)
- Pure lifestyle/vlog content

## Preferred analyst depth
- Cost budget: high — user waits 10+ min willingly
- Source range: prefers niche creators over mainstream

## Prior findings worth remembering
- Concluded Portugal NHR is closing — updated 2025-Q3
- Identified creator X as unreliable on tax topics (2 factual errors)
```

Update rule: after each ANALYST session with `confidence_score > 0.8`, extract three signals and merge into the AGENTS.md via a small LLM call.

---

## 10. State schema

```python
# schemas/youtube/state.py — extend AdaptiveRAGState

class AnalystState(TypedDict):
    # Inherited / shared with AdaptiveRAGState
    question: str
    thread_id: str
    channel_ids: list[str]
    generation: str             # final conclusion
    citations: list[dict]

    # Analyst-specific
    analyst_budget_seconds: int         # hard limit, default 900
    session_root: str                   # studies/analyst/<thread_id>/<ts>/
    user_agents_md: str                 # loaded at session start
    todos: Annotated[list[dict], operator.add]        # accumulated plan
    findings: Annotated[list[dict], operator.add]     # accumulated notes
    subagent_calls: Annotated[list[dict], operator.add]  # audit trail
    deep_agent_event_log_path: str      # path to transcript.jsonl
```

---

## 11. API surface

### Request
`POST /api/v1/youtube/agents/search`
```json
{
  "question": "Build a dossier on Wealthy Expat: core claims, contradictions, bias indicators",
  "thread_id": "abc-123",
  "force_mode": "analyst",
  "channel_ids": ["UC49PyeVkVY6godW0pF6H8Pg"],
  "analyst_budget_seconds": 900
}
```

Returns immediately with `{analyst_session_id, stream_endpoint, artifacts_endpoint}`.

### Stream
`GET /api/v1/youtube/agents/analyst/{session_id}/stream` (SSE)

Event types:
```
data: {"type": "todo_added", "item": {id, description}}
data: {"type": "subagent_started", "name": "contradiction_finder", "input": {...}}
data: {"type": "subagent_finished", "name": "contradiction_finder", "output": {...}}
data: {"type": "finding_written", "section": "contradictions", "snippet": "..."}
data: {"type": "citation_added", "citation": {video_id, title, url, quote}}
data: {"type": "conclusion", "answer": "...", "confidence": 0.87}
data: {"type": "done"}
```

### Artifacts
`GET /api/v1/youtube/agents/analyst/{session_id}/artifacts`

Returns a tree listing of the session's filesystem output plus download URLs for each file.

### Cancel
`DELETE /api/v1/youtube/agents/analyst/{session_id}` → revokes the Celery task.

---

## 12. Cost & latency

Per-session expectations (8Gi worker, 19-model fallback chain):

| Budget | Typical run | Max run |
|---|---|---|
| LLM calls | 30–80 | 200 |
| NVIDIA NIM embeddings | 10–30 | 100 |
| Cypher queries | 5–20 | 50 |
| Disk writes | 10–40 KB | 200 KB |
| Wall clock | 3–10 min | 15 min (budget cap) |
| Rough cost (if 100% API-priced on Sonnet 4.6) | $0.20–1.00 | $2–3 |
| Rough cost on current NIM+Groq free tier | **$0** | $0 |

Budget enforcement: worker tracks elapsed time; if > `analyst_budget_seconds`, agent is interrupted and asked to write `conclusion.md` with whatever findings it has.

---

## 13. Implementation sequence (for when this is built)

1. **Prerequisite:** Reddit Ingest + Reddit Analyst Mode ships first. Builds the analyst pattern on fresh data.
2. **Port:** Copy Reddit's analyst skeleton to `services/youtube/analyst/` — subagents + skills swap out; tools swap to YouTube's retriever + Cypher + creator profile.
3. **Graph integration:** Extend `graphs/youtube/adaptive.py::route_by_mode` to accept `"analyst"` and dispatch to a new `run_analyst` node that enqueues the Celery task instead of invoking the graph inline.
4. **Celery task:** `tasks/youtube/analyst.py` — async agent run, streams events to Redis pub/sub channel `youtube:analyst:<session_id>`.
5. **SSE endpoint:** `routers/v1/youtube/analyst.py` — subscribes to the Redis channel, forwards as SSE.
6. **Artifacts endpoint:** serves files from `studies/analyst/<thread_id>/<ts>/` via MinIO (or local FS on dev).
7. **Auto-classifier update:** extend `CLASSIFY_PROMPT` with ANALYST signal keywords; retrain expectation of when DEEP vs ANALYST fires.
8. **AGENTS.md persistence:** new PG table `user_analyst_profile (user_id, agents_md_content, updated_at)`.
9. **Skill library:** write the 4 initial skills (`detect-contradictions.md`, `trace-evolution.md`, `rank-evidence-quality.md`, `build-creator-dossier.md`).
10. **Eval corpus:** curate 10 analyst-grade questions against the existing 457-video corpus as a regression suite.

Estimated effort: ~1.5–2 weeks after Reddit Analyst Mode is done (most pattern code reused).

---

## 14. Open questions / risks

| Risk | Mitigation |
|---|---|
| Agent loops indefinitely on a failing tool call | Hard time budget + max-tool-calls cap enforced by DeepAgents middleware |
| Context explosion on long runs | DeepAgents filesystem offloads notes; subagent context quarantine prevents main-agent bloat |
| Unbounded cost on Sonnet routing | NIM free tier absorbs most traffic; Sonnet only for final synthesis via manual routing rule |
| Users expect instant answers | Explicit UX copy: "ANALYST mode runs for 3–15 min. We'll notify you when done." + progress stream |
| Cypher tool injection | Whitelist `MATCH...RETURN` patterns; reject any `CREATE/DELETE/SET/MERGE/DROP` |
| AGENTS.md drift (over-confident memory) | Confidence decay: entries older than 90 days require re-validation before reuse |
| Redundant work across sessions | Filesystem layer lookup: before dispatch, check if same question was answered in another session's `conclusion.md` recently |

---

## 15. What ANALYST does NOT do

Explicit non-goals — if you want these, use other modes:

- **Quick factual lookups** → FAST
- **One-off citation-backed answers** → STANDARD
- **Fixed-angle comparison across a small set** → DEEP
- **Real-time streaming analysis of live streams** → out of scope
- **Modifying the corpus / writing to Neo4j or Qdrant** → ingestion pipelines only
- **Cross-tenant analysis** → same user, same thread, same channel scope — no multi-user aggregation
- **Financial trading / betting decisions** → separate future product (Polymarket agent)

---

## 16. References

- [`ADAPTIVE-RAG-ARCHITECTURE.md`](./ADAPTIVE-RAG-ARCHITECTURE.md) — current 3-mode design this extends
- [`AGENTIC-RAG-ARCHITECTURE.md`](./AGENTIC-RAG-ARCHITECTURE.md) — base RAG pipeline
- [`INTEGRATION-PATTERN-DeepAgents-LangGraph.md`](./INTEGRATION-PATTERN-DeepAgents-LangGraph.md) — DeepAgents + LangGraph integration patterns used here
- [`AGENTIC-RAG-TESTS.md`](./AGENTIC-RAG-TESTS.md) — endpoint test patterns (ANALYST tests will mirror these)
- [LangChain DeepAgents blog](https://blog.langchain.com/deep-agents/) — agent harness primitives
- [Context Management for Deep Agents (Jan 2026)](https://www.langchain.com/blog/context-management-for-deepagents) — filesystem offloading, subagent isolation
- [Building Multi-Agent Applications with Deep Agents (Jan 2026)](https://blog.langchain.com/building-multi-agent-applications-with-deep-agents/) — general-purpose subagent pattern

---

## 17. Status & ownership

| Field | Value |
|---|---|
| Author | 2026-04-19 |
| Status | Design complete, implementation deferred |
| Blocks on | Knowledge Distiller completion → Textbook-to-Code Agent → Reddit Ingest + Analyst Mode |
| Estimated effort once unblocked | 1.5–2 weeks |
| Owner | Rafael |
