# YCS Port Continuation — 2026-06-06 (post-3d)

**Status:** Live continuation of [`YCS-PORT-PLAN-2026-06-06.md`](YCS-PORT-PLAN-2026-06-06.md).
Authored after Wave 3d landed clean (RAG graphs); Wave 4 partially started
(only `domains/ycs/es_index/params.py` written). Conversation compacted
to free context budget.

---

## 1. Progress snapshot

| Wave | Status | Notes |
|---|---|---|
| **1** — Delete additions | ✅ DONE | 1,876 LOC baseline, 32 files |
| **2** — ES + Neo4j infra | ✅ DONE | `infra/elasticsearch/` + `infra/neo4j/` + `app.py` lifespan wired |
| **3a** — Embeddings/chunker/grader/reranker/cache | ✅ DONE | 721 LOC, 20 files |
| **3b** — Conversation/graph_builder/ingestion | ✅ DONE | 1,079 LOC, 15 files |
| **3c** — Retrievers (ES/Qdrant/Neo4j/Smart) | ✅ DONE | 649 LOC, 9 files |
| **3d** — RAG graphs (standard + adaptive) | ✅ DONE | 1,463 LOC, 54 files (every node is a directory) |
| **4** — Celery tasks + routers | 🟡 PARTIAL | Only `es_index/params.py` started |
| **5** — FastHTML | ⏳ PENDING | Fresh code (deprecated had no YCS UI) |

**Live `/api/v1/ycs/content/search` endpoint** survives from Wave 1.
Everything else is broken until Wave 4 reintroduces the agents + content
dispatch endpoints.

---

## 2. Hard constants for the next session

**MUST OBEY** (`docs/CODE-CONVENTIONS.md` + `feedback_port_fidelity` +
`feedback_free_tier_only`):

1. Port from `zdeprecated/apps/fastapi/.../youtube/` **1:1**. No
   substitutions (Neo4j stays Neo4j), no SOTA upgrades, no novel
   features. Code-convention restructuring is allowed; functional
   changes are NOT.
2. **NIM via the deprecated direct path** in YCS modules (e.g. embeddings
   calls `https://integrate.api.nvidia.com/v1` directly with retry —
   NOT the rotator). Other LLM calls in `graph_builder/neo4j` task
   use the deprecated `with_fallbacks(...)` chain of `ChatOpenAI`
   instances pointing at NIM + Groq.
3. **No paid SaaS providers anywhere.** Free tier only.
4. Every module follows `docs/CODE-CONVENTIONS.md` §2-§4 split:
   `node.py`/`service.py`/`domain.py`/`schemas.py`/`prompts.py`/
   `params.py`/`keys.py`/`patterns.py`/`errors.py`/`__init__.py`.
5. **Explicit `__all__`** in every `__init__.py`.
6. **Cite deprecated provenance** in every module + function docstring
   (file path + line range).

---

## 3. Wave 4 — remaining items (FILE-BY-FILE)

### 4.0 · ES bulk-index module (in flight)

**Path:** `apps/fastapi/domains/ycs/es_index/`

**Status:** `params.py` written (BULK_REFRESH, INDEXED_STATUSES).

**Files to add:**

- `service.py` — port `helpers.py:L1778-1859` `index_videos_to_elasticsearch` + `index_transcriptions_to_elasticsearch`
  - Both: build `[{"index": {...}}, doc, ...]` ops; `await es.bulk(operations=..., refresh=BULK_REFRESH)`; count hits whose `item["index"]["status"] in INDEXED_STATUSES`; return `{indexed, failed, errors}` shape on success or `{indexed:0, failed:len(...), error:str(e)}` on exception
  - Use `INDEX_METADATA` / `INDEX_TRANSCRIPTIONS` from `infra/elasticsearch.params`
- `__init__.py` — re-export `index_videos_to_elasticsearch`, `index_transcriptions_to_elasticsearch`

### 4.1 · Playwright transcript service (BIG — `helpers.py:L1179-1723`)

**Path:** `apps/fastapi/domains/ycs/transcript/`

**Conventions split:**

- `params.py` — `CDP_HEADED` + `CDP_HEADLESS` env-read URLs (default the
  deprecated values from `helpers.py` top), `MAX_CONCURRENT=5`,
  `CONTEXT_POOL_SIZE=5`, `TIMEOUT_MS=30000`, `NAV_TIMEOUT_MS=60000`,
  `BROWSER_REFRESH_INTERVAL=15`, `MAX_RETRIES=2`,
  `CONNECT_TIMEOUT_S=30.0`, `INITIAL_RETRY_WAIT_S=5.0`,
  `RETRY_LIMIT=6` (browser refresh retries)
- `errors.py` — `TranscriptError`, `CDPConnectError`,
  `NoTranscriptFoundError`
- `domain.py` — pure helpers ported from helpers.py:
  - `_get_cdp_websocket_url(endpoint)` (sync HTTP probe + URL parse)
  - any pure text-cleanup helpers if found
- `service.py` — port `PlaywrightTranscriptService` class
  (`helpers.py:L1179-1723`) PLUS the module-level
  `init_transcript_service` / `close_transcript_service` /
  `fetch_transcriptions_batch` functions. **Big file — keep as one
  service.py per port-fidelity (deprecated kept it together).** Keep
  comments preserved.
- `__init__.py` — re-export the public API
  (`PlaywrightTranscriptService`, `init_transcript_service`,
  `close_transcript_service`, `fetch_transcriptions_batch`)

**Add to `pyproject.toml`:** `playwright>=1.40,<2.0` and `tenacity>=8,<10`
(deprecated uses retry decorators).

**`Dockerfile.fastapi` add:** `RUN playwright install --with-deps chromium`
(or rely on the cluster Playwright service — `CDP_HEADED` points at it,
so we DON'T need chromium in the container; just the playwright Python
package).

**Test reachability** at `playwright-headed.playwright.svc.cluster.local:9222`.

### 4.2 · Celery task wrappers

All four live as **single files** (deprecated didn't split them — each
task is ~50-80 LOC):

#### `apps/fastapi/domains/ycs/extract/task.py` (REPLACES the deleted Wave-1 file)

Port `zdeprecated/tasks/youtube/crawler.py:L41-292`. Three Celery tasks:

```
extract_videos(video_ids, include_transcription=True, languages=None)
extract_channel(channel_id, max_results=0, include_transcription=True, languages=None)
extract_playlist(playlist_id, max_results=0, include_transcription=True, languages=None)
```

Each:
1. `asyncio.run(_extract_*_async(...))` — Celery is sync, async work wrapped
2. Inside: fresh `AsyncElasticsearch` client (env-read), call
   `YtDlpExtractor.extract_batch/channel/playlist`
3. `index_videos_to_elasticsearch(es, videos)` from Wave 4.0
4. If `include_transcription`: init Playwright service (from Wave 4.1),
   `fetch_transcriptions_batch`, `index_transcriptions_to_elasticsearch`,
   close service
5. Always: `await es.close()`
6. Task decorator: `@app.task(bind=True, name="domains.ycs.extract.task.extract_*")`
7. Use `self.update_state(state="PROGRESS", meta={...})` mid-task

**Imports**: from `infra.celery import app`, `domains.ycs.extract import YtDlpExtractor`, `domains.ycs.transcript import (init/close/fetch...)`, `domains.ycs.es_index import index_videos_to_elasticsearch, index_transcriptions_to_elasticsearch`.

#### `apps/fastapi/domains/ycs/qdrant_task/task.py`

Port `tasks/youtube/qdrant.py`. Two tasks:

```
ingest_to_qdrant(video_ids=None, chunk_size=2000, chunk_overlap=200)
invalidate_cache()
```

- `ingest_to_qdrant`: opens AsyncElasticsearch + AsyncQdrantClient, calls
  `domains.ycs.ingestion.ingest_to_qdrant(...)`, closes both
- `invalidate_cache`: opens `redis.asyncio` from REDIS env, calls
  `domains.ycs.cache.invalidate_cache(r)`, closes

**Module path**: `domains/ycs/qdrant_task/` (NOT just `qdrant` — that
collides with the `qdrant_client` package). Add `__init__.py` + `task.py`.

#### `apps/fastapi/domains/ycs/neo4j_task/task.py`

Port `tasks/youtube/neo4j.py`. One task:

```
ingest_to_neo4j(video_ids=None, batch_size=3)
```

Inside `_run()`:
1. AsyncElasticsearch (env-read)
2. `Neo4jGraph(url=NEO4J_URI, username=..., password=...)` — deprecated
   did NOT set `refresh_schema=False` here (only in app.py); preserve
   that omission per port-fidelity
3. Build deprecated 13-model `with_fallbacks` chain. **Use the EXACT
   deprecated model list** (`L86-101` of deprecated `neo4j.py`):
   Groq: `llama-3.3-70b-versatile`, `qwen/qwen3-32b`, `llama-3.1-8b-instant`
   NIM: `z-ai/glm5`, `moonshotai/kimi-k2.5`, `moonshotai/kimi-k2-instruct`, `deepseek-ai/deepseek-v3.2`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`, `meta/llama-3.3-70b-instruct`, `meta/llama-3.1-8b-instruct`
   `temperature=0.0`, `max_retries=0`, `timeout=120` (Groq) / `600` (NIM)
4. `fetch_transcripts_from_es` + `fetch_metadata_from_es` from
   `domains.ycs.ingestion`
5. `build_video_metadata_graph(neo4j_graph, video_metadata)` from
   `domains.ycs.graph_builder`
6. `extract_and_store_graph(transcripts=..., metadata_map=..., llm=...,
   neo4j_graph=..., batch_size=...)` from `domains.ycs.graph_builder`
7. `await es.close()`

**Module path**: `domains/ycs/neo4j_task/` (avoid name collision with
the `neo4j` package).

#### `apps/fastapi/domains/ycs/pipeline_task/task.py`

Port `tasks/youtube/pipeline.py:L17-56`. ONE task:

```
full_channel_pipeline(channel_id, max_results=0, include_transcription=True,
                      include_qdrant=True, include_graph=False)
```

Uses `celery.chain(*steps)`:
- `steps = [extract_channel.si(channel_id, max_results, include_transcription)]`
- `if include_qdrant: steps.append(ingest_to_qdrant.si())`
- `if include_graph:  steps.append(ingest_to_neo4j.si())`
- `steps.append(invalidate_cache.si())`
- `result = chain(*steps).apply_async()`
- Return `{pipeline_id: result.id, steps: [s.name for s in steps], channel_id}`

**Imports the task functions** from `domains.ycs.{extract,qdrant_task,neo4j_task}.task`.

### 4.3 · Celery task registration

**`apps/fastapi/infra/celery/params.py`** — add to `TASK_INCLUDE`:

```python
"domains.ycs.extract.task",
"domains.ycs.qdrant_task.task",
"domains.ycs.neo4j_task.task",
"domains.ycs.pipeline_task.task",
```

The existing `Q_YCS` queue + glob route `domains.ycs.*` → `Q_YCS`
remains correct.

### 4.4 · API: `api/v1/ycs/content/router.py` (REVISION)

**Current state**: only `POST /search` exists.

**Add** (port `zdeprecated/routers/v1/youtube/content.py:L70-129`):

- `POST /videos` → calls `domains.ycs.extract.task.extract_videos.delay(...)` → returns `{task_id, status: "queued", endpoint: f"/api/v1/tasks/{task.id}"}`
- `POST /channel` → calls `extract_channel.delay(...)` → same response shape
- `POST /playlist` → calls `extract_playlist.delay(...)` → same response shape

**Request bodies**: import the existing `VideosRequest`, `ChannelRequest`,
`PlaylistRequest` from `domains.ycs.extract` (already shipped Wave 1
revision). `include_transcription` + `transcription_languages` fields
are already there.

### 4.5 · API: `api/v1/ycs/agents/router.py` (NEW)

Port `zdeprecated/routers/v1/youtube/agents.py:L42-298`. **7 endpoints:**

1. **`PUT /config`** — accepts `LLMConfig` (port from
   `schemas/youtube/inputs.py:L16-22`), persists to Redis JSON
   `coelhonexus:youtube:agents:config`. Strict port — even though
   YCS's NIM key is now BYOK in the rotator, deprecated did this
   verbatim. Uses `request.app.state.redis_aio` (lifespan needs to
   provision this — see §6 below).

2. **`POST /search`** — agentic RAG (non-stream). Port lines L62-149:
   - If `thread_id` empty or `"default"`: check
     `domains.ycs.cache.get_cached_response(r, question, force_mode)`.
     Return cached + `_from_cache=True` if hit.
   - `history = await get_history(pg_url, thread_id)` (Wave 3b
     conversation module)
   - Build the adaptive graph via the helper (see §4.6 below)
   - Build `initial_state` shape (port the 15-field dict L89-105 verbatim)
   - `config = {"configurable": {"thread_id", "max_retries"}, "recursion_limit": 100}`
   - `result = await graph.ainvoke(initial_state, config=config)`
   - Build response: answer, mode, citations, grounded,
     retrieval_sources, retry_count, search_query (deep extras when
     mode == "deep": sub_questions, confidence_score)
   - `await save_turn(pg_url, thread_id, question, answer, mode)`
   - Cache when thread is default

3. **`POST /search/stream`** (SSE) — port lines L153-223:
   - Same initial_state + config as above
   - `async for event in graph.astream(initial_state, config=config, stream_mode="updates")` → for each `(node_name, update)` emit `data: {json}\n\n`
   - Track `last_generation`; after loop, `save_turn` if non-empty
   - Final event: `data: {"node": "end", "status": "complete"}\n\n`
   - `StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})`
   - Helper `_serialize_update` lifted from deprecated `helpers.py` (a few lines, slugifies Documents to dicts)

4. **`POST /ingest/qdrant`** — port lines L226-242. Takes `IngestRequest`,
   calls `ingest_to_qdrant.delay(video_ids, chunk_size, chunk_overlap)`,
   returns `{task_id, status: "queued", endpoint: f"/api/v1/tasks/{id}"}`

5. **`POST /ingest/neo4j`** — port lines L245-260. Takes
   `GraphIngestRequest`, calls `ingest_to_neo4j.delay(video_ids,
   batch_size)`, same response shape.

6. **`GET /graph/stats`** — port lines L263-275. Calls
   `get_graph_stats(request.app.state.neo4j_graph)` from
   `domains.ycs.graph_builder`. `app.state.neo4j_graph` needs to be
   provisioned in lifespan (see §6).

7. **`POST /pipeline`** — port lines L278-298. Takes `PipelineRequest`,
   calls `full_channel_pipeline.delay(...)`, same response shape.

**Aggregate** in `apps/fastapi/api/v1/ycs/__init__.py`:

```python
router.include_router(_content_router, prefix="/content")
router.include_router(_agents_router,  prefix="/agents")
```

**Schemas to port** (request bodies — only the ones not already on disk):
- `LLMConfig` → `apps/fastapi/api/v1/ycs/agents/schemas.py` (BaseModel)
- `RAGSearchRequest` → same file
- `IngestRequest`, `GraphIngestRequest`, `PipelineRequest` → same file

Port `zdeprecated/schemas/youtube/inputs.py:L16-22, L124-138, L143-176`
verbatim. Field shapes are unchanged from deprecated.

### 4.6 · Build-graph helper

Deprecated `routers/v1/youtube/helpers.py:_build_graph(request)`:

```python
def _build_graph(request):
    """Build the adaptive RAG graph with all dependencies from app.state."""
    return build_adaptive_rag_graph(
        retriever     = request.app.state.smart_retriever,
        grader        = request.app.state.grader,
        llm           = request.app.state.llm,            # rotator chain
        checkpointer  = None,
        neo4j_graph   = request.app.state.neo4j_graph,
    )
```

This pulls dependencies that **must be provisioned in `app.py`
lifespan** (see §6).

`_serialize_update` (deprecated `helpers.py`):

```python
def _serialize_update(node_name, update):
    """Convert LangGraph state-patch dict to JSON-serializable."""
    serialized = {"node": node_name}
    for k, v in update.items():
        if k == "documents":
            serialized[k] = [
                {"page_content": d.page_content[:500], "metadata": d.metadata}
                for d in (v or [])
            ]
        else:
            serialized[k] = v
    return serialized
```

Put both helpers in `apps/fastapi/api/v1/ycs/agents/build.py`.

---

## 5. Wave 5 — FastHTML (detailed)

Wave 5 is allowed fresh code (deprecated had no YCS UI). Follow the
DD pattern (`apps/fasthtml/features/dd/`):

### 5.1 · Source step

`apps/fasthtml/features/ycs/source/body.py` — UPDATE (currently has
only Search live):

- Activate Videos / Channel / Playlist tabs (currently `_PendingTab`)
- Each tab posts to `/api/v1/ycs/content/{videos,channel,playlist}` with
  `include_transcription` checkbox + `transcription_languages`
  comma-separated input
- Submit → POST returns `{task_id}` → redirect to
  `/youtube-content-search/ingest?task={task_id}`

### 5.2 · Ingest step

`apps/fasthtml/features/ycs/ingest/body.py` — REPLACE the Wave-1
placeholder with TWO panels:

**(a) Active job progress** — when `?task=<id>` is in URL, JS polls
`Celery AsyncResult` status (deprecated `GET /api/v1/tasks/{id}`
endpoint — port that too in Wave 4.7 if not already there). Show
progress bar + state.

**(b) ES-driven library list** — server-side BFF calls
`/api/v1/ycs/admin/ingested-channels` + `/ingested-playlists` (NEW
backend endpoints — Wave 5.4) which run ES aggregations:

```python
# Channels with at least one video
await es.search(
    index = INDEX_METADATA,
    size = 0,
    aggs = {"by_channel": {
        "terms": {"field": "channel_id", "size": 1000},
        "aggs": {"name": {"top_hits": {"size": 1, "_source": ["channel", "channel_id"]}}}
    }}
)
```

Render as cards with channel name + video count.

### 5.3 · Ask step

`apps/fasthtml/features/ycs/ask/body.py` — REPLACE the Wave-1
placeholder:

- Chat input textarea
- Mode pill (Auto / Fast / Standard / Deep) — sets `force_mode` in POST
- Channel multi-select (populated by GET `/admin/ingested-channels`)
- Submit → POST JSON to `/api/v1/ycs/agents/search/stream`
- JS consumes SSE, renders node-by-node updates: `{node: "retrieve"}` →
  show "Retrieving documents...", `{node: "generate", generation: "..."}` →
  stream into a markdown bubble, `{node: "end"}` → close
- Citations panel: render `result.citations` as clickable cards

### 5.4 · LLM config form

Small form inside settings or as a panel on Ask step. POST to `PUT
/api/v1/ycs/config`. Fields: provider, model, temperature, base_url,
api_key.

Mirror the BYOK rotator settings form style
(`features/settings/llm.py`).

### 5.5 · New admin endpoints (Wave 5 only — fresh code OK)

`apps/fastapi/api/v1/ycs/admin/router.py` — NEW:

- `GET /admin/ingested-channels` — ES aggregation grouped by channel_id, return `{channel_id, channel_name, video_count}` list
- `GET /admin/ingested-playlists` — same shape, grouped by playlist_id
- `GET /admin/task/{task_id}` — wraps Celery `AsyncResult(task_id)` → `{state, meta, result}` (for the Ingest step polling)

Conventions split:
```
api/v1/ycs/admin/
├── __init__.py
└── router.py
```

Register in `api/v1/ycs/__init__.py`:
```python
router.include_router(_admin_router, prefix="/admin")
```

---

## 6. `app.py` lifespan additions (required by Wave 4)

The agents router reads from `request.app.state.*`. Wave 2 already
added ES + Neo4j, but more state needs provisioning:

```python
# In lifespan, AFTER existing init:

# Redis async client (for cache + agents config)
import redis.asyncio as redis_aio
app.state.redis_aio = redis_aio.from_url(redis_url_from_env())

# Postgres URL (for conversation history)
app.state.pg_url = postgres_url_from_env()

# YCS conversation table — idempotent create
from domains.ycs.conversation import ensure_conversation_table
await ensure_conversation_table(app.state.pg_url)

# Neo4j graph (LangChain wrapper) — for graph/stats + adaptive classify
from infra.neo4j import get_graph as get_neo4j_graph
app.state.neo4j_graph = get_neo4j_graph()

# Smart retriever — composed from ES + Qdrant + Neo4j
from domains.ycs.retriever import (
    SmartRetriever, ElasticsearchRetriever,
    QdrantHybridRetriever, Neo4jRetriever,
)
from infra.elasticsearch import get_es
from infra.qdrant import get_qdrant
from domains.ycs.embeddings import (
    create_dense_embeddings, create_sparse_embeddings,
)
es = get_es()
qdrant = get_qdrant()
es_retriever = ElasticsearchRetriever(es)
qdrant_retriever = QdrantHybridRetriever(
    qdrant = qdrant,
    dense_embeddings = create_dense_embeddings(),
    sparse_embeddings = create_sparse_embeddings(),
)
# llm for the neo4j retriever's entity extraction — share with the rest
# (one ChatOpenAI instance pointing at NIM, deprecated convention)
app.state.llm = build_deprecated_llm_chain()  # see Wave 4.x note
neo4j_retriever = Neo4jRetriever(
    neo4j_graph = app.state.neo4j_graph,
    llm = app.state.llm,
)
app.state.smart_retriever = SmartRetriever(
    es_retriever = es_retriever,
    qdrant_retriever = qdrant_retriever,
    neo4j_retriever = neo4j_retriever,
)

# Grader
from domains.ycs.grader import DocumentGrader
app.state.grader = DocumentGrader(app.state.llm)
```

`build_deprecated_llm_chain()` lives in
`apps/fastapi/api/v1/ycs/agents/llm_chain.py` and ports the 13-model
fallback chain from `tasks/youtube/neo4j.py:L86-103`:
- Groq: llama-3.3-70b, qwen3-32b, llama-3.1-8b
- NIM: glm5, kimi-k2.5, kimi-k2-instruct, deepseek-v3.2,
  nemotron-super-49b, llama-3.3-70b, llama-3.1-8b
- `primary.with_fallbacks(rest)` returns the chain

ES bulk-index `ensure_indexes` already runs in Wave 2 lifespan.

---

## 7. Decision points / known issues

### 7.1 · Playwright dependency

Wave 4.1 needs `playwright` Python package + (sidecar Playwright service
at `playwright-headed.playwright.svc.cluster.local:9222`). Sidecar
exists in COELHO Cloud already; just add the Python dep.

### 7.2 · LLM "rotator vs direct" tension

The port-fidelity rule says use deprecated's direct `ChatOpenAI(...).
with_fallbacks(...)`. The rotator (`domains.llm.rotator.chain`) is
the new project-wide pattern. **Per the spec: direct, NOT rotator.**
The rotator is only used by DD (and the YCS embeddings module that
already uses NIM REST direct, NOT rotator).

### 7.3 · "Tasks status" endpoint

Deprecated `routers/v1/youtube/content.py` returns
`endpoint: f"/api/v1/tasks/{task.id}"` in its response — implying a
shared `/api/v1/tasks/` router. Wave 5.5 adds
`/api/v1/ycs/admin/task/{id}` as the YCS-scoped version.

### 7.4 · Task module names

Two task modules collide with library names if named naively:
- `domains/ycs/neo4j/task.py` → collides with `neo4j` package
- `domains/ycs/qdrant/task.py` → collides with `qdrant_client`

Resolution: name them `neo4j_task/` and `qdrant_task/`. The Celery
`name=` field still uses the deprecated stem `tasks.youtube.qdrant.*`
or the new module path — pick one and stick with it. Recommended:
new name `domains.ycs.qdrant_task.task.*` (consistent with the file
location).

---

## 8. Quick-start checklist for next session

When the conversation is compact-friendly again:

1. **Mark Task #21 still `in_progress`** (Wave 4)
2. Read `apps/fastapi/domains/ycs/es_index/params.py` (already exists)
3. Write `es_index/service.py` + `es_index/__init__.py` (§4.0)
4. Read `zdeprecated/.../helpers.py:L1179-1723` (Playwright)
5. Write `transcript/{params,errors,domain,service,__init__}.py` (§4.1)
6. Add `playwright>=1.40,<2.0` + `tenacity>=8,<10` to `pyproject.toml`
7. Write `extract/task.py`, `qdrant_task/task.py`, `neo4j_task/task.py`,
   `pipeline_task/task.py` (§4.2)
8. Update `infra/celery/params.py` TASK_INCLUDE (§4.3)
9. Update `api/v1/ycs/content/router.py` (§4.4)
10. Write `api/v1/ycs/agents/{router,schemas,build,llm_chain}.py` (§4.5,§4.6)
11. Update `api/v1/ycs/__init__.py` aggregator
12. Update `app.py` lifespan (§6)
13. `python -c "import ast; ..."` syntax check
14. Mark Task #21 `completed`, Task #22 `in_progress`
15. Wave 5 (§5)

---

## 9. References

- [`docs/CODE-CONVENTIONS.md`](CODE-CONVENTIONS.md) — the rulebook
- [`docs/YCS-PORT-PLAN-2026-06-06.md`](YCS-PORT-PLAN-2026-06-06.md) — the
  authoritative spec (this doc continues it)
- `apps/fastapi/domains/dd/synth/nodes/sawc/` — convention pilot
- `apps/fastapi/domains/ycs/` — what's already shipped
- `zdeprecated/apps/fastapi/.../youtube/` — ground truth
- `[[feedback_port_fidelity]]` — port-1:1 mandate
- `[[feedback_free_tier_only]]` — NIM via rotator only
- `[[feedback_youtube_transcript]]` — yt-dlp metadata, Playwright transcripts
- `[[project_ycs_port_plan_2026_06_06]]` — memory pointer to spec
