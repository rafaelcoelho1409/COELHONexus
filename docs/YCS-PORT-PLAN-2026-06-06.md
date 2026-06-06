# YCS Port Plan — 2026-06-06

**Status:** AUTHORITATIVE port spec. Supersedes every prior YCS plan in
this repo, including [`docs/YCS-MIGRATION-SOTA-2026-05-19.md`](YCS-MIGRATION-SOTA-2026-05-19.md)
(historical; do NOT apply its deltas) and the 13-slice ship that landed
2026-06-05 (drifted beyond deprecated and is being reverted).

**Authored by:** Claude session 2026-06-06 under explicit user mandate
"migrate only what was in `zdeprecated/`, no suggestions, no SOTA, no
substitutions; FastHTML is the only place fresh code is welcome."

---

## 1. Hard rules

1. **Port only.** Migrate exactly what exists in
   `zdeprecated/apps/fastapi/{routers,services,graphs,schemas,tasks}/youtube/`.
   No new features. No SOTA upgrades. No substitutions (Neo4j stays Neo4j,
   not FalkorDB). No reinvented persistence (ES + Neo4j + Qdrant + Postgres
   + Redis, not MinIO libraries).
2. **Code conventions are mandatory.** Every module obeys
   [`docs/CODE-CONVENTIONS.md`](CODE-CONVENTIONS.md). The conventions doc
   is the rulebook, not advisory. `apps/fastapi/domains/dd/synth/nodes/sawc/`
   is the pilot and the canonical shape.
3. **Free models only.** All inference via the NIM rotator
   (`domains.llm.rotator.chain`). No Jina, Cohere, OpenAI, Anthropic, or
   any paid SaaS. NIM is the only inference provider. See
   `[[feedback_free_tier_only]]`.
4. **FastHTML is fresh.** The deprecated repo had only sidebar stubs for
   YCS. FastHTML pages are built fresh, mirroring DD's pattern
   (`apps/fasthtml/features/dd/`). Free design.

---

## 2. Code-conventions recap

Every YCS leaf module follows the §2-4 split from
[`docs/CODE-CONVENTIONS.md`](CODE-CONVENTIONS.md):

| File | Holds |
|---|---|
| `node.py` | LangGraph shell — ~20-30 lines wrapping `service.run(state)` |
| `service.py` | Async I/O orchestration; calls `domain.*` + LLM rotator + storage |
| `domain.py` | PURE functions only (no `await`, no I/O, no clocks) |
| `schemas.py` | Pydantic for LLM/HTTP boundaries |
| `entities.py` | `@dataclass` value objects |
| `state.py` | LangGraph `TypedDict` |
| `keys.py` | Storage/Redis key builders (functions) |
| `params.py` | Loose numeric tunables |
| `config.py` | `@dataclass(frozen=True, slots=True)` grouped tunables |
| `prompts.py` | LLM prompt strings + version markers |
| `patterns.py` | Pre-compiled regex |
| `versions.py` | Schema/prompt cache-bust strings |
| `errors.py` | Exception classes |
| `__init__.py` | Public API re-exports with explicit `__all__` |

**Anti-patterns (do NOT):**

- Behavior on a config dataclass — values only (§3, §7)
- Pydantic `BaseModel` for internal config (§7)
- `constants.py` / `types.py` catch-all names (§2)
- Direct `os.environ["NVIDIA_API_KEY"]` — use `domains.llm.rotator.chain.*`
- I/O inside `domain.py` — domain is pure (§4)
- `from neo4j import GraphDatabase` reaching into `domain.py` (§4)
- "Service" as a dumping ground — pure helpers move to `domain.py` (§7)

---

## 3. Deprecated → new module shape

For every deprecated single-file module, the port lives as a DIRECTORY
following the §5 pilot shape:

| Deprecated file | New module |
|---|---|
| `services/youtube/cache.py` | `domains/ycs/cache/{service,keys,params,__init__}.py` |
| `services/youtube/chunker.py` | `domains/ycs/chunker/{service,params,__init__}.py` |
| `services/youtube/embeddings.py` | `domains/ycs/embeddings/{service,params,patterns,__init__}.py` |
| `services/youtube/grader.py` | `domains/ycs/grader/{service,schemas,prompts,versions,__init__}.py` |
| `services/youtube/reranker.py` | `domains/ycs/reranker/{service,params,__init__}.py` |
| `services/youtube/conversation.py` | `domains/ycs/conversation/{service,keys,params,__init__}.py` |
| `services/youtube/graph_builder.py` | `domains/ycs/graph_builder/{service,domain,schemas,patterns,params,prompts,__init__}.py` |
| `services/youtube/ingestion.py` | `domains/ycs/ingestion/{service,domain,keys,params,__init__}.py` |
| `services/youtube/retriever.py` | `domains/ycs/retriever/{elasticsearch,qdrant_hybrid,neo4j,smart,domain,schemas,prompts,__init__}.py` |
| `graphs/youtube/rag.py` | `domains/ycs/rag/standard/{graph,state,prompts,__init__}.py` + one folder per node under `nodes/` |
| `graphs/youtube/adaptive.py` | `domains/ycs/rag/adaptive/{graph,state,prompts,__init__}.py` + one folder per node under `nodes/` |
| `schemas/youtube/inputs.py` | Each request schema co-located with its router (`api/v1/ycs/{feature}/schemas.py`) |
| `schemas/youtube/state.py` | `domains/ycs/rag/{standard,adaptive}/state.py` |
| `schemas/youtube/agents.py` | Co-located with consumer (`grader/schemas.py`, `graph_builder/schemas.py`, etc.) |
| `tasks/youtube/crawler.py` | `domains/ycs/extract/task.py` (single file; routes by mode) |
| `tasks/youtube/qdrant.py` | `domains/ycs/qdrant/task.py` |
| `tasks/youtube/neo4j.py` | `domains/ycs/neo4j/task.py` |
| `tasks/youtube/pipeline.py` | `domains/ycs/pipeline/task.py` |
| `routers/v1/youtube/content.py` | `api/v1/ycs/content/router.py` |
| `routers/v1/youtube/agents.py` | `api/v1/ycs/agents/router.py` |
| `routers/v1/youtube/helpers.py` | SPLIT into `domains/ycs/extract/` (yt-dlp metadata) + `domains/ycs/transcript/` (Playwright transcript service) + `domains/ycs/elasticsearch/index.py` (ES dual-index write) |

---

## 4. Port roadmap — 5 waves, ordered

### Wave 1 · Delete the additions (revert deviations from deprecated)

| # | Target | Why |
|---|---|---|
| 1.1 | Delete `apps/fastapi/domains/ycs/sota/` | Not in deprecated. SOTA wave was my invention from [`YCS-MIGRATION-SOTA-2026-05-19.md`](YCS-MIGRATION-SOTA-2026-05-19.md), explicitly out of scope per the port-only mandate. |
| 1.2 | Delete `apps/fastapi/infra/falkordb/` + `apps/fastapi/domains/ycs/graph/` | FalkorDB was my substitution. Deprecated used Neo4j. |
| 1.3 | Delete `apps/fastapi/domains/ycs/progress/` | Deprecated tracked progress via vanilla Celery `task_id` + `AsyncResult`. My Redis throttle + cancel system is new. Update `extract/task.py`, `transcript/task.py`, `index/qdrant/task.py`, `api/v1/ycs/extract/router.py` to use Celery's native `AsyncResult.state` polling. |
| 1.4 | Delete `apps/fastapi/domains/ycs/storage/` + `apps/fastapi/api/v1/ycs/libraries/` | No MinIO layer in deprecated. Search history, video metadata blob, library manifest, library index — all mine. Persistence belongs in ES (Wave 2). |
| 1.5 | Delete `apps/fastapi/domains/ycs/chunk/domain.py` (custom splitter); call `langchain_text_splitters.RecursiveCharacterTextSplitter` directly from `domains/ycs/chunker/service.py` | Deprecated used the langchain class. Already in `pyproject.toml`. |
| 1.6 | KEEP `apps/fastapi/domains/ycs/extract/` (yt-dlp metadata — already correct). REPLACE `apps/fastapi/domains/ycs/transcript/` (yt-dlp `--write-auto-subs`, wrong tool) with a port of deprecated `PlaywrightTranscriptService` (`helpers.py:L1179-1723`). The two modules stay separate (different tools, different sidecars) but the Celery tasks in Wave 4.1 call BOTH per request. | Deprecated split: yt-dlp = metadata, Playwright = transcripts. See §5. |
| 1.7 | Delete my Slice 2 search-history routes (`GET /content/search`, `GET /content/search/{id}`) | Not in deprecated; mine. |
| 1.8 | Delete `apps/fastapi/api/v1/ycs/index/` and `apps/fastapi/api/v1/ycs/retrieve/` debug routers | My additions (Slices 5-6). Deprecated exposed Qdrant ingest through `agents.py:POST /ingest/qdrant` (single endpoint) and never exposed a debug-retrieval endpoint. |

### Wave 2 · Provision deprecated infra (ES + Neo4j alongside existing Qdrant + Redis + Postgres)

| # | Target | Source |
|---|---|---|
| 2.1 | Add to `pyproject.toml`: `elasticsearch[async]>=8,<9`, `neo4j>=5,<6`, `langchain_neo4j>=0.1,<1.0`, `flashrank>=0.2,<1.0` | Deprecated pyproject + service imports |
| 2.2 | Add Elasticsearch pod to the Helm chart (`infra/coelho-cloud/`); service exposes 9200 in-cluster | Deprecated `ELASTICSEARCH_HOST/USERNAME/PASSWORD` env reads |
| 2.3 | Add Neo4j pod to the Helm chart with APOC plugin enabled; service exposes 7687 in-cluster | Deprecated `NEO4J_URI/USERNAME/PASSWORD` env reads |
| 2.4 | `apps/fastapi/infra/elasticsearch/{service,params,__init__}.py` — `AsyncElasticsearch` client factory, `ensure_indexes()` provisions the two deprecated indexes | Deprecated `app.py:L105-113` |
| 2.5 | `apps/fastapi/infra/neo4j/{service,params,__init__}.py` — async driver + `Neo4jGraph` wrapper (`refresh_schema=False` per deprecated rationale: APOC schema reflection stalls 25-45s) | Deprecated `app.py:L146-167` |
| 2.6 | `apps/fastapi/app.py` lifespan — initialize ES + Neo4j alongside the existing Qdrant + Redis + Postgres setup | Deprecated `app.py:L89-281` |

### Wave 3 · Port deprecated services (1:1, conventions-compliant)

| # | Target module | Deprecated source |
|---|---|---|
| 3.1 | `domains/ycs/embeddings/` — `embed_via_nim_async` direct API call + retry + 50-batch pacing + FastEmbedSparse BM25 sidecar; DO NOT route through the rotator (deprecated didn't) | `services/youtube/embeddings.py:L36-194` |
| 3.2 | `domains/ycs/chunker/` — wrap `RecursiveCharacterTextSplitter` (`chunk_size=2000`, `chunk_overlap=200`, separators `["\n\n", "\n", ". ", " ", ""]`); pure `chunk_documents(docs)` helper | `services/youtube/chunker.py:L31-92` |
| 3.3 | `domains/ycs/grader/` — `DocumentGrader` class with `chain = GRADING_PROMPT \| llm.with_structured_output(GradeResult, method="function_calling")`; parallel `asyncio.gather` per doc | `services/youtube/grader.py:L22-64` |
| 3.4 | `domains/ycs/reranker/` — FlashRank `Ranker()` lazy-loaded, `rerank(query, docs, top_k)` adds `rerank_score` to metadata | `services/youtube/reranker.py:L22-80` |
| 3.5 | `domains/ycs/cache/` — SHA-256 keyed Redis cache, 1h TTL; `_cache_key(question, mode)` builder; `get_cached_response`, `cache_response`, `invalidate_cache` (single + prefix scan); `CACHE_PREFIX = "coelhonexus:rag:cache:"` (exact deprecated string) | `services/youtube/cache.py:L27-98` |
| 3.6 | `domains/ycs/conversation/` — Postgres `conversation_history` table; `ensure_conversation_table(pg_url)`, `get_history(pg_url, thread_id, limit=10)`, `save_turn(pg_url, thread_id, q, a, mode)`. **Replaces** the DD AsyncPostgresSaver path I shipped. | `services/youtube/conversation.py:L19-72` |
| 3.7 | `domains/ycs/graph_builder/` — `LLMGraphTransformer` setup (`node_properties=True, relationship_properties=True, strict_mode=False`); rapidfuzz fuzzy merge at 75% cutoff with NUMERIC_LABELS skip; 2s inter-batch pacing; `extract_and_store_graph`, `build_video_metadata_graph`, `get_graph_stats` | `services/youtube/graph_builder.py:L33-315` |
| 3.8 | `domains/ycs/ingestion/` — ES → Qdrant streaming pipeline; `_scroll_transcripts(es, video_ids, batch_size=50)` async generator; collection auto-create with `vectors_config={"dense": VectorParams(size=dim, distance=COSINE)}` + `sparse_vectors_config={"sparse": SparseVectorParams(...)}`; deterministic point id via `md5(f"{video_id}_{chunk_index}")` | `services/youtube/ingestion.py:L44-264` |
| 3.9 | `domains/ycs/retriever/elasticsearch.py` — `ElasticsearchRetriever`: multi_match on `content` field with channel_ids term filter; separate `_fetch_metadata` call | `services/youtube/retriever.py:L42-109` |
| 3.10 | `domains/ycs/retriever/qdrant_hybrid.py` — `QdrantHybridRetriever`: dense + sparse in a SINGLE `query_points` call with `Prefetch` per vector + `FusionQuery(fusion=Fusion.RRF)`; channel_id pre-filter via `FieldCondition` | `services/youtube/retriever.py:L115-230` |
| 3.11 | `domains/ycs/retriever/neo4j.py` — `Neo4jRetriever`: LLM extracts entities via `ENTITY_EXTRACTION_PROMPT \| llm.with_structured_output(ExtractedEntities)`; two-step Cypher (direct entity match + one-hop neighbors); LIST-id normalization in Cypher (`CASE WHEN valueType(e.id) STARTS WITH "LIST" THEN head(e.id) ELSE e.id END`); BELONGS_TO channel filter | `services/youtube/retriever.py:L236-409` |
| 3.12 | `domains/ycs/retriever/smart.py` — `SmartRetriever` orchestrator: parallel `asyncio.gather` of all three with `return_exceptions=True`; dedup by `(video_id, chunk_index, content[:100])`; FlashRank rerank; ES-fallback when Qdrant + Neo4j both fail or return zero | `services/youtube/retriever.py:L415-513` |
| 3.13 | `domains/ycs/rag/standard/` — `YouTubeContentGraph`: retrieve → grade → generate → hallucination → cite, with rewrite loop; conditional edges per deprecated logic; LLM via the rotator's chat chain (deprecated used `ChatOpenAI` directly with fallbacks — the rotator IS the new equivalent) | `graphs/youtube/rag.py:L41-308` |
| 3.14 | `domains/ycs/rag/adaptive/` — `AdaptiveRAGGraph`: contextualize → classify → fast/standard/deep; DEEP fans out via `Send()`; channel auto-detect node (resolves names to IDs via Neo4j Cypher) populates `channel_ids` before STANDARD dispatch | `graphs/youtube/adaptive.py:L58-466` |

### Wave 4 · Port deprecated tasks + endpoints

| # | Target | Source |
|---|---|---|
| 4.1 | `domains/ycs/extract/task.py` revision — collapse to deprecated shape: `extract_videos(video_ids, include_transcription, languages)`, `extract_channel(channel_id, max_results, include_transcription, languages)`, `extract_playlist(playlist_id, max_results, include_transcription, languages)`. Each does yt-dlp metadata + (if requested) Playwright transcript + ES dual-index write. | `tasks/youtube/crawler.py:L57-292` |
| 4.2 | `domains/ycs/qdrant/task.py` — `ingest_to_qdrant(video_ids, chunk_size, chunk_overlap)`, `invalidate_cache()`. Reads transcripts from ES, not MinIO. | `tasks/youtube/qdrant.py:L22-98` |
| 4.3 | `domains/ycs/neo4j/task.py` — `ingest_to_neo4j(video_ids, batch_size)`. Reads transcripts from ES. 13-model fallback chain (Groq → NVIDIA NIM) per deprecated rationale — but routed through the existing rotator, NOT direct `ChatOpenAI`. | `tasks/youtube/neo4j.py:L22-133` |
| 4.4 | `domains/ycs/pipeline/task.py` revision — `full_channel_pipeline(channel_id, max_results, include_transcription, include_qdrant, include_graph)` as a Celery `chain(...)`: extract_channel → ingest_to_qdrant (if include_qdrant) → ingest_to_neo4j (if include_graph) → invalidate_cache | `tasks/youtube/pipeline.py:L17-56` |
| 4.5 | `api/v1/ycs/content/router.py` revision — `POST /search` (sync), `POST /videos`, `POST /channel`, `POST /playlist` (queue Celery tasks; return `task_id`) | `routers/v1/youtube/content.py:L29-129` |
| 4.6 | `api/v1/ycs/agents/router.py` (new) — `PUT /config`, `POST /search`, `POST /search/stream` (SSE), `POST /ingest/qdrant`, `POST /ingest/neo4j`, `GET /graph/stats`, `POST /pipeline` | `routers/v1/youtube/agents.py:L42-298` |
| 4.7 | Drop my `api/v1/ycs/rag/` routes (`/ask`, `/ask/stream`, `/ask/adaptive`, `/ask/adaptive/stream`); they're renamed equivalents of `agents/search*`. Standardize on deprecated naming. | — |
| 4.8 | Drop my `api/v1/ycs/pipeline/router.py:POST /channel`; the one true endpoint is `POST /pipeline` on the agents router (per deprecated). | — |
| 4.9 | Update `infra/celery/params.py` — TASK_INCLUDE registers `domains.ycs.{extract,qdrant,neo4j,pipeline}.task`; route `domains.ycs.*` → `Q_YCS` (already wired). | — |

### Wave 5 · FastHTML — fresh code (the one place additions are welcome)

Mirror `apps/fasthtml/features/dd/` shape — feature folder with
`routes.py + page.py + cache.py + shared/{nav,toolbar,urls}.py + sub-feature folders with body.py`.

| # | Page | Posts to / reads |
|---|---|---|
| 5.1 | **Source step** (`features/ycs/source/body.py`) — 4 mode tabs: Search / Videos / Playlist / Channel. Each non-Search tab has `library_name` (free text, becomes user label), `include_transcription` checkbox, `transcription_languages` comma-separated. | `POST /api/v1/ycs/content/{search,videos,channel,playlist}` |
| 5.2 | **Ingest step** (`features/ycs/ingest/body.py`) — TWO panels: (a) active extract jobs (Celery `AsyncResult.state` poll); (b) channels + playlists with ingested videos (driven by ES aggregations: `GET /api/v1/ycs/admin/ingested-channels`, `/ingested-playlists` — new BFF endpoints querying ES). | Polls Celery + ES aggregations |
| 5.3 | **Ask step** (`features/ycs/ask/body.py`) — chat input + mode pill (Auto / Fast / Standard / Deep) + channel-scope multi-select against the ES channel list. SSE-driven live updates per node. | `POST /api/v1/ycs/agents/search/stream` |
| 5.4 | **LLM config form** in `features/settings/` or as a small panel on Ask step — POST to `PUT /api/v1/ycs/config`. Form fields: provider, model, temperature, base_url, api_key. | `PUT /api/v1/ycs/config` |

CSS already exists at `apps/fasthtml/static/css/ycs/ycs.css` (8988 bytes,
3-step wizard styles). Re-use; the page IDs already align.

---

## 5. Transcript + metadata stack — RESOLVED (2026-06-06)

User clarified: Playwright and yt-dlp are **complementary**, not
alternatives. The 2026-05-19 feedback memory revision claiming
"yt-dlp PRIMARY for transcripts" was speculative and was never adopted
by the deprecated YCS code. See revised [[feedback_youtube_transcript]]
(REV 2026-06-06).

**Division of labor (per deprecated `helpers.py`):**

| Tool | Job | Deprecated source | New module |
|---|---|---|---|
| **yt-dlp** | Video METADATA (title, channel, duration, view counts, thumbnails, chapters) via `--dump-json`. Used by `search`, `extract_video`, `extract_batch`, `extract_playlist`, `extract_channel`. | `helpers.py:L53-546` (`YtDlpExtractor`) | `domains/ycs/extract/` (already shipped — keep) |
| **Playwright** | TRANSCRIPTS via DOM scrape. Pool of 5 browser contexts, CDP connection to a Playwright sidecar, JS DOM scrape with 4 fallback selectors, browser refresh every 10-15 videos, exponential-backoff retry. | `helpers.py:L1179-1723` (`PlaywrightTranscriptService`) | `domains/ycs/transcript/` (REPLACE my current yt-dlp `--write-auto-subs` code with the Playwright port) |

**Crawler tasks call both**: yt-dlp first for metadata, then (if
`include_transcription=True`) Playwright for transcripts, then ES
dual-index write. See `tasks/youtube/crawler.py:L57-292` (deprecated)
for the integration shape.

**Sidecars needed:**
- bgutil-PoT (`localhost:4416`) — yt-dlp PO Token for metadata
  extraction of age/PoT-gated videos. Pyproject already pins the
  package; Helm needs the sidecar manifest.
- Playwright (`playwright.playwright.svc.cluster.local:9224` headless,
  `:9222` headed). The deprecated code probed both. YouTube blocks
  headless for transcripts so `:9222` is the live one.

---

## 6. References

- [`docs/CODE-CONVENTIONS.md`](CODE-CONVENTIONS.md) — module organization
  rulebook. **Every YCS module obeys this.**
- `zdeprecated/apps/fastapi/{routers,services,graphs,schemas,tasks}/youtube/` —
  canonical functionality being ported.
- `apps/fastapi/domains/dd/synth/nodes/sawc/` — convention pilot. Copy
  the leaf shape per module.
- [`docs/YCS-MIGRATION-SOTA-2026-05-19.md`](YCS-MIGRATION-SOTA-2026-05-19.md) —
  historical SOTA design. **DO NOT APPLY.** Kept for archaeology only.
- `[[project_code_org_sota_2026_05_20]]` — the "port not rewrite"
  architectural decision that this plan honors.
- `[[feedback_free_tier_only]]` — no paid SaaS; NIM only.
- `[[feedback_port_fidelity]]` — port deprecated as-is, no
  substitutions / suggestions / additions.
- `[[feedback_youtube_transcript]]` — Playwright transcript path is
  known-broken; resolution deferred to runtime, not port-time.
