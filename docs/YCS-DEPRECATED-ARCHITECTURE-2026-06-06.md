# YCS — deprecated architecture (port-recall, 2026-06-06)

What the **old** YouTube Content Search built under `zdeprecated/apps/`, condensed
to one document so we don't have to re-derive it during the port. Pairs with
[YCS-PORT-PLAN-2026-06-06.md](./YCS-PORT-PLAN-2026-06-06.md) (the forward-
looking 5-wave port) and supersedes nothing — both stay live until the port
lands and this file moves to `docs/archive/`.

All `path:line` citations are inside `zdeprecated/apps/`.

---

## TL;DR

3 stages, each its own HTTP entrypoint:

| Stage | Surface | Nature |
|---|---|---|
| **Source** | `POST /api/v1/youtube/search` | **Sync.** yt-dlp metadata search — just *finds* candidate videos. |
| **Ingest** | `POST /api/v1/youtube/videos\|channel\|playlist` → 3-task Celery chain | **Fire-and-poll.** Extracts → Qdrant → Neo4j. |
| **Ask** | `POST /api/v1/youtube/agents/search` (and `/stream`) | **LangGraph RAG** with conversation memory + adaptive routing. |

5-store data matrix:

| Store | Holds |
|---|---|
| **Elasticsearch** | `coelhonexus-youtube-metadata` + `coelhonexus-youtube-transcriptions` (raw) |
| **Qdrant** | Hybrid (dense + sparse) chunk embeddings for primary retrieval |
| **Neo4j** | Document nodes + extracted entities/relations for graph traversal |
| **Postgres** | `conversation_history` rows (thread_id, question, answer, mode, created_at) |
| **Redis** | RAG response cache (`rag:cache:<sha256(question)>`, 1h TTL) + Celery progress |

---

## Stage 1 — Source (sync browse)

`fastapi/routers/v1/youtube/content.py:29-67`. Sync handler that shells `yt-dlp`
for metadata only — title, channel, duration, upload date, views,
`webpage_url`. **No download, no transcripts, no indexing.** Pure browse.

The request schema `SearchRequest` (`fastapi/schemas/youtube/inputs.py:28-96`)
maps the user's filters straight to yt-dlp's native filter syntax: regex /
contains operators on title, view-count ranges, date windows, live status.
This is why it stays so fast — the work is all in yt-dlp's subprocess, no
intermediate parse.

---

## Stage 2 — Ingest (3-phase Celery chain)

Chain wired in `fastapi/tasks/youtube/pipeline.py:17-56` using LangChain's
immutable `.si()` signatures so each phase can route to its own queue and
survive worker restarts. Kicked off by `POST /videos`, `/channel`, or
`/playlist`; returns a `task_id` immediately. Clients poll
`/api/v1/tasks/{id}` for progress.

### Phase A — Extraction (`tasks/youtube/crawler.py:225-292`)

Worker calls `asyncio.run()` to bridge into async code (FastAPI-reload-safe):

1. Fetch metadata via yt-dlp.
2. Index metadata to ES `coelhonexus-youtube-metadata`.
3. Spawn Playwright (`max_concurrent=5`, `refresh_interval=10` — undocumented
   but **load-bearing** to defeat stale CDP sessions on long batches) and
   scrape transcripts → ES `coelhonexus-youtube-transcriptions`.

Progress is streamed live via `self.update_state()`.

### Phase B — Vector ingestion (`tasks/youtube/qdrant.py:22-75`)

Streams transcripts one-at-a-time from ES (`services/youtube/ingestion.py:
81-116` — ES scroll API, OOM-proof at any dataset size), chunks at ~2000
chars (`services/youtube/chunker.py`), embeds with **dual vectors**:

- **Dense** — NVIDIA NIM API (remote)
- **Sparse** — local FastEmbed BM25

…and upserts to a Qdrant **hybrid** collection with deterministic IDs for
idempotence (`services/youtube/ingestion.py:49-52`).

> **Port note.** A port that only ships dense embeddings will silently drop
> BM25 recall. Both vector types are needed at query time.

### Phase C — Knowledge graph (`tasks/youtube/neo4j.py:22-133`)

Sends each **full transcript as one Document** to an LLM for
entity/relationship extraction — not chunked first. Design choice:
~352 LLM calls instead of ~2911 chunks. Fallback chain is Groq → NIM
(600 s timeout for long transcripts). Post-processing with `rapidfuzz`
handles entity dedup.

> **Port note.** Don't naively "chunk then extract" — explicit cost/quality
> trade against full-transcript extraction.

---

## Stage 3 — Ask (LangGraph RAG)

Entry: `fastapi/routers/v1/youtube/agents.py:61-223`. Streaming variant uses
LangGraph's `astream(stream_mode="updates")` → SSE that yields **node-by-node
progress, not tokens** — the UI surfaces which graph node is running.

> **Port note.** Do not "upgrade" to token streaming. The UI depends on
> per-node events.

Initial state (`schemas/youtube/state.py:30-59`): `question`, `thread_id`,
`channel_ids` (scope), empty `documents` and `citations`.

### The graph is two-tiered

**Adaptive tier** (`graphs/youtube/adaptive.py`) classifies the question into:

- **FAST** — skip retrieval, direct LLM answer.
- **STANDARD** — one RAG pass.
- **DEEP** — planner emits subagents; each runs its own RAG pass in parallel.

**Core RAG pipeline** (`graphs/youtube/rag.py:41-99`), identical for STANDARD
and each DEEP subagent:

```
retrieve → grade → generate → check_hallucination → format_citations
                                       └── (fail) ──┐
                                                    ▼
                                              rewrite_query → retrieve …
```

Per-node detail:

- **retrieve** — `SmartRetriever` (`services/youtube/retriever.py:42-98`)
  fans out to Qdrant hybrid (dense + sparse, deduped by `video_id`), Neo4j
  graph traversal over the entity graph, and ES keyword fallback. Results
  merged.
- **grade** — `DocumentGrader` (`services/youtube/grader.py:22-64`) runs an
  LLM relevance check on each candidate doc **in parallel**.
- **generate** — LLM consumes the graded docs with a formatted context
  block (`graphs/youtube/rag.py:80-101`).
- **check_hallucination** — LLM-as-judge pass against the source docs
  (`graphs/youtube/rag.py:104-139`). Failure routes back to `rewrite_query`.
- **format_citations** — dedup by `(title, webpage_url)`
  (`graphs/youtube/rag.py:142+`).

> **Port note.** `state.retry_count` guards the rewrite_query loop. Preserve
> it or the graph will spin.

### Conversation memory

Lives in Postgres (`conversation_history`: `thread_id`, `question`, `answer`,
`mode`, `created_at`). Non-default `thread_id` triggers
`get_history` (`services/youtube/conversation.py:35-54`) to pull last 10
Q&A pairs; `contextualize_question` (`graphs/youtube/adaptive.py:64-92`)
rewrites multi-turn prompts like "tell me more" into standalone form
**before retrieval**. `save_turn` persists after generation.

### Response cache

Redis key `rag:cache:<sha256(question)>`, 1-hour TTL
(`services/youtube/cache.py`). Wraps the **whole graph invocation**.

---

## Things easy to break in a naive port (consolidated)

1. **Drop one of the two Qdrant vector types** → BM25 recall silently dies.
2. **Chunk before entity extraction** → 8× LLM-call inflation; loses
   cross-section entity coherence.
3. **Remove Playwright `refresh_interval=10`** → CDP sessions go stale on
   long ingestion batches.
4. **Switch SSE to token streaming** → the UI's per-node progress display
   breaks (it was never reading tokens).
5. **Skip `state.retry_count` on the hallucination → rewrite loop** →
   infinite spin on a stubbornly ungrounded question.
6. **Move adaptive classification client-side** → DEEP mode's planner +
   parallel subagents only work because they share the LangGraph state;
   splitting them across a UI round-trip drops the parallelism.

---

## File map (for jump-to-source)

```
fastapi/
  routers/v1/youtube/
    content.py    — Source HTTP surface (POST /search, /videos, /channel, /playlist)
    agents.py     — Ask HTTP surface (POST /agents/search, /agents/search/stream)
    helpers.py    — shared route helpers
  tasks/youtube/
    pipeline.py   — the 3-phase .si() chain
    crawler.py    — Phase A: yt-dlp + Playwright + ES write
    qdrant.py     — Phase B: chunk + hybrid embed + Qdrant upsert
    neo4j.py      — Phase C: full-transcript entity extraction + Neo4j write
  services/youtube/
    ingestion.py  — ES scroll API + idempotent Qdrant upsert
    chunker.py    — chunking policy (~2000 chars)
    embeddings.py — dense (NIM) + sparse (FastEmbed BM25) factories
    retriever.py  — SmartRetriever (Qdrant + Neo4j + ES merge)
    grader.py     — parallel doc-relevance LLM grader
    reranker.py   — secondary rerank pass
    graph_builder.py — Neo4j extraction prompts + post-process
    conversation.py  — Postgres conversation_history
    cache.py         — Redis rag:cache wrapper
  schemas/youtube/
    inputs.py     — SearchRequest, IngestRequest, AskRequest
    outputs.py    — response shapes (answer + citations + sources)
    state.py      — LangGraph state (question, documents, retry_count, …)
    prompts.py    — system prompts for each LLM call site
    graph.py      — Neo4j entity/relation pydantic types
    agents.py     — adaptive-tier classification schemas
  graphs/youtube/
    rag.py        — core RAG StateGraph (retrieve → … → format_citations)
    adaptive.py   — adaptive tier (FAST/STANDARD/DEEP + planner subagents)
    helpers.py    — shared graph utilities
fasthtml/
  routes/, components/, services/ — UI surface (search results, ingest progress, ask chat)
```
