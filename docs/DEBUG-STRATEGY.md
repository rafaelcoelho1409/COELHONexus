# Docs Distiller — Debug Strategy

Two-phase strategy: per-stage REST endpoints for the existing httpx-based
ingestion (already wired) plus a checkpoint-replay plan for the LangGraph
phase that lands next (Planner → Synth → Critic → Assembler).

## Phase 1 — Ingestion (live)

Each tier is an isolated, side-effect-tracked function. Debug surface
adds nothing new conceptually — it just exposes the same functions as
synchronous HTTP endpoints so you can curl them individually without
spawning Celery tasks or fighting the single-flight lock.

### Endpoints

All under `/api/v1/docs-distiller/debug` (FastAPI port 23020 in dev):

| Endpoint | Purpose |
|---|---|
| `POST /resolve/{slug}` | Returns the resolver's `best_source` pick + all available sources for the slug. Fast sanity check on `sources.yaml`. |
| `POST /ingest/{slug}?tier=N` | Runs **one tier** in isolation (N ∈ 1..5). Writes to the canonical MinIO path — overwrites prior bodies. No Celery, no lock, no progress UI side effects. |
| `POST /post/{slug}` | Re-runs `post.apply_to_store` against the existing MinIO content. Tune `SPLIT_MIN_SECTION_BYTES` / monolith thresholds without re-downloading. |
| `POST /finalize/{slug}` | Re-writes the canonical `manifest.json` from the existing in-memory state. Use when the manifest payload shape changes. |
| `POST /snapshot/{slug}?label=…` | Server-side copies the current `ingestion/{slug}/` content into `ingestion/{slug}/_snapshots/{ts}-{label}/`. Frozen point you can restore from. |
| `GET /snapshots/{slug}` | List snapshot timestamps (newest first). |
| `POST /restore/{slug}?ts=…` | Overwrites canonical with the named snapshot (deletes current canonical first, preserves the `_snapshots/` subtree). |
| `DELETE /snapshot/{slug}?ts=…` | Drop a snapshot. |

### Typical debug loops

**Tuning Tier 4 URL filters.** Snapshot → ingest with tier=4 → diff manifest → tweak filter constants → ingest with tier=4 again → diff again. If a tweak is bad, restore.

```bash
curl -XPOST localhost:23020/api/v1/docs-distiller/debug/snapshot/docker?label=pre-filter-tweak
# edit filters.py
curl -XPOST 'localhost:23020/api/v1/docs-distiller/debug/ingest/docker?tier=4'
curl localhost:23020/api/v1/docs-distiller/ingestion/docker/manifest | jq .page_count
# if worse:
curl -XPOST 'localhost:23020/api/v1/docs-distiller/debug/restore/docker?ts=20260516T180000Z-pre-filter-tweak'
```

**Tuning the monolith splitter.** Skip ingest entirely — change the constant in `post.py`, then:

```bash
curl -XPOST localhost:23020/api/v1/docs-distiller/debug/post/docker
```

Runs in ~2 seconds against MinIO-cached bodies. No network, no Celery, no `sources.yaml`.

### What's NOT in this layer (deliberately)

- **Auto-snapshots before every stage** — would balloon MinIO usage for not-much-benefit when bodies are tiny. Take snapshots manually when about to tune something risky.
- **Diff endpoint** — `jq` on two `/ingestion/{slug}/manifest` payloads is already fine.
- **Env-gating** — single-user cluster. Add a `KD_DEBUG_ENDPOINTS=1` gate before any multi-tenant exposure.

## Phase 2 — LangGraph (when Planner / Synth / Critic / Assembler land)

LangGraph debugging in 2026 has a strong native answer that we should
lean on rather than rebuild:

| Layer | What it gives you |
|---|---|
| **PostgresSaver checkpointer** | Every super-step (node execution) writes a checkpoint row. Thread-based grouping. Free time-travel: re-enter the graph at any past checkpoint with any state edit. |
| **LangSmith tracing** | Every node = one nested run under the graph run. Tree view in the LangSmith UI shows node-by-node state transitions, durations, LLM I/O. |
| **LangGraph Studio (local Docker)** | Interactive time-travel: click a past checkpoint, edit state, fork a new execution. Visual graph + checkpoint browser. |

### Decisions to lock in when the graph code starts

1. **Checkpointer = `langgraph.checkpoint.postgres.AsyncPostgresSaver`** — not `MemorySaver`. Postgres is already in the helm stack (see `k8s/helm/values.yaml`). Persists across pod restarts; required for any non-trivial time-travel debug.

2. **`thread_id` = the framework run identifier**, e.g. `{slug}/{experience_level}/{run_id}`. Groups every node's checkpoints under one thread so all the planner/synth/critic/assembler super-steps for one run are queryable as a unit.

3. **LangSmith tracing on by default in dev**:
   ```bash
   LANGSMITH_TRACING=true
   LANGSMITH_API_KEY=…           # in coelhonexus-secret
   LANGSMITH_PROJECT=docs-distiller-dev
   LANGSMITH_ENDPOINT=https://api.smith.langchain.com   # or self-hosted
   ```
   Add to `k8s/helm/templates/_helpers.tpl::commonEnvVars`.

4. **Recursion + timeout limits per graph**:
   ```python
   graph.compile(checkpointer=…, recursion_limit=50)
   await graph.ainvoke(state, {"configurable": {"thread_id": …}, "recursion_limit": 50})
   ```
   Fails fast on infinite loops; otherwise the checkpoint table can grow without bound.

5. **Nightly checkpoint cleanup** — delete checkpoint rows for threads older than N days. Postgres write amplification is real; one paragraph in a cron job is enough.

### Debug endpoints to add at graph time

Symmetric to ingestion's `/debug`:

| Endpoint | Purpose |
|---|---|
| `GET /debug/graph/{thread_id}/state` | Current state for a thread |
| `GET /debug/graph/{thread_id}/history` | Every checkpoint (super-step) in the thread, newest first |
| `POST /debug/graph/{thread_id}/replay?checkpoint_id=…` | Re-enter at that checkpoint with no edit (re-run forward) |
| `POST /debug/graph/{thread_id}/edit?checkpoint_id=…` (body: state patch) | Edit state at that checkpoint then re-run forward — the "fork" pattern from Studio, available programmatically |

Underlying machinery is just `checkpointer.aget_state(config)` / `graph.update_state(config, values)` / `graph.ainvoke(None, config)` — three lines per endpoint.

### What we explicitly DON'T need to build

- A custom node-by-node trace viewer — LangSmith already does this better than anything we'd write
- A custom checkpoint store — Postgres + PostgresSaver covers durability
- A custom "stop at this node" debugger — Studio + `interrupt_before=[…]` handles it
- A custom replay UI — Studio handles it; programmatic replay through the debug endpoints handles automation/regression

### One-line install for when we're ready

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async with AsyncPostgresSaver.from_conn_string(pg_url) as checkpointer:
    await checkpointer.setup()    # idempotent, creates tables
    graph = builder.compile(checkpointer=checkpointer)
```

The deprecated app already had this exact pattern in `app.py` lifespan (`AsyncPostgresSaver.from_conn_string(PG_URL)`) — port it to the new `app.py` lifespan when the graph code lands.

## Sources

- [LangGraph Persistence — LangChain docs](https://docs.langchain.com/oss/python/langgraph/persistence)
- [How to Use LangSmith in 2026 — AI.cc](https://www.ai.cc/blogs/how-to-use-langsmith-2026-complete-guide/)
- [LangGraph Debugging with LangSmith Tracing — machinelearningplus](https://machinelearningplus.com/gen-ai/langgraph-observability-debugging-langsmith-tracing/)
- [Debugging Non-Deterministic LLM Agents with LangGraph Time Travel — DEV](https://dev.to/sreeni5018/debugging-non-deterministic-llm-agents-implementing-checkpoint-based-state-replay-with-langgraph-5171)
- [Troubleshooting & Debugging — LangGraph Cheatsheet](https://sumanmichael.github.io/langgraph-cheatsheet/cheatsheet/troubleshooting-debugging/)
- [Deploy LangGraph to Production: A Step-by-Step Tutorial 2026 — rapidclaw.dev](https://rapidclaw.dev/blog/deploy-langgraph-production-tutorial-2026)
- [checkpoints — LangChain Reference](https://reference.langchain.com/python/langgraph/checkpoints)
