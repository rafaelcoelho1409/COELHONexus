# Agentic RAG Architecture — COELHONexus

> Complete architecture overview for the YouTube Content Search Agentic RAG system.

## Core Stack

```
ES (raw data) → Qdrant (semantic search) → Neo4j (knowledge graph)
     ↑                    ↑                        ↑
  Crawlers            Embeddings              LLM extraction
  (Celery)          (NVIDIA NIM API)         (NVIDIA NIM API)
                          ↓
                    SmartRetriever (parallel) → LangGraph Agent → Answer
                          ↓
                    Redis (cache + checkpoints)
```

## Retrieval Sources

| Service | Role | How it works |
|---------|------|-------------|
| **Qdrant** | Semantic search | Vector similarity (cosine distance) — finds text that **means** the same thing as the question |
| **Neo4j** | Knowledge graph | Graph traversal (relationships) — finds entities that are **connected** and multi-hop reasoning |
| **Elasticsearch** | Fallback | Full-text keyword search — used when Qdrant/Neo4j are unavailable |

### How They Work Together

Qdrant and Neo4j solve **different dimensions of the same question**:

| | Qdrant | Neo4j |
|---|---|---|
| **What it finds** | Text that **means** the same thing | Entities that are **connected** |
| **Answers** | "What content is relevant?" | "How are things related?" |
| **Strength** | Fuzzy semantic matching | Multi-hop reasoning |

**Example: "What does Wealthy Expat think about Dubai for crypto investors?"**

- **Qdrant** finds transcript chunks containing similar text about Dubai + crypto + tax
- **Neo4j** finds entity connections: (Person: Wealthy Expat) → (Topic: Dubai) → (Topic: Crypto) → (Topic: Tax Free)
- **LLM** combines both → grounded answer with deeper insight

Both run in **parallel** via `asyncio.gather` in the SmartRetriever.

## Data Pipeline

### Ingestion Flow

```
YouTube Video
  → yt-dlp extracts metadata       → Elasticsearch (metadata index)
  → Playwright extracts transcript  → Elasticsearch (transcriptions index)
                                          ↓
                                    POST /agents/ingest (Celery)
                                          ↓
                                    Chunk (2000 chars, 200 overlap)
                                          ↓
                              ┌───────────┴───────────┐
                              ▼                       ▼
                        NVIDIA NIM API          FastEmbed BM25
                        (dense, 2048d)          (sparse, local)
                              │                       │
                              └───────────┬───────────┘
                                          ▼
                                    Qdrant (hybrid collection)

                                    POST /agents/ingest/neo4j (Celery)
                                          ↓
                                    LLMGraphTransformer (14-model fallback)
                                          ↓
                                    Neo4j (knowledge graph)
```

### Search Flow (Agentic RAG)

```
User Question
  ↓
LangGraph StateGraph:
  1. RETRIEVE → SmartRetriever (Qdrant + Neo4j in parallel, ES fallback)
  2. GRADE → LLM evaluates document relevance (parallel)
  3. GENERATE → LLM produces answer with citations
  4. CHECK HALLUCINATION → LLM verifies answer is grounded
  5. FORMAT CITATIONS → Extract video title + URL
  ↓
  If grading fails → REWRITE QUERY → retry from step 1
  If hallucination detected → REWRITE QUERY → retry from step 1
  ↓
Answer + Citations + Grounding Status
```

## Infrastructure Services

| Service | Location | Role |
|---------|----------|------|
| **Elasticsearch** | COELHOCloud | Raw data storage (video metadata + transcriptions) |
| **Qdrant** | COELHOCloud | Vector database (hybrid dense + sparse search) |
| **Neo4j** | COELHOCloud | Graph database (knowledge graph with APOC) |
| **Redis Stack** | COELHOCloud | Cache (RAG responses), LangGraph checkpointing, Celery broker |
| **Flower** | COELHOCloud | Celery task monitoring dashboard |
| **Playwright** | COELHOCloud | Remote browser for transcript extraction (CDP) |
| **FastAPI** | COELHONexus | API server (sync search + task enqueue) |
| **Celery Worker** | COELHONexus | Background tasks (crawling, ingestion, graph extraction) |

## LLM Configuration

### Chat/Completion (Agentic RAG)

14-model fallback chain via NVIDIA NIM (same API key):
1. z-ai/glm5 (Arena ELO 1451)
2. moonshotai/kimi-k2.5 (Arena ELO 1447)
3. moonshotai/kimi-k2-instruct
4. moonshotai/kimi-k2-thinking
5. deepseek-ai/deepseek-v3.2 (Arena ELO 1421)
6. nvidia/nemotron-3-super-120b-a12b
7. qwen/qwen3.5-122b-a10b
8. nvidia/llama-3.3-nemotron-super-49b-v1.5
9. mistralai/mistral-small-4-119b-2603
10. google/gemma-4-31b-it
11. meta/llama-4-maverick-17b-128e-instruct
12. meta/llama-3.3-70b-instruct
13. qwen/qwen3-next-80b-a3b-instruct
14. meta/llama-3.1-8b-instruct

### Embedding (Vector Generation)

Single model with retry: `nvidia/llama-nemotron-embed-1b-v2` (2048d, 8K context)
- Configurable via `NVIDIA_EMBEDDING_MODEL` env var
- See `docs/NVIDIA-NIM-EMBEDDING-MODELS.md` for all tested models

## API Endpoints

| Endpoint | Mode | Purpose |
|----------|------|---------|
| `POST /content/search` | Sync | Search YouTube (fast, no indexing) |
| `POST /content/videos` | Celery | Extract + index videos |
| `POST /content/channel` | Celery | Extract + index channel |
| `POST /content/playlist` | Celery | Extract + index playlist |
| `POST /agents/search` | Sync | Agentic RAG search |
| `POST /agents/search/stream` | Sync SSE | Streaming RAG search |
| `POST /agents/ingest/qdrant` | Celery | ES → Qdrant (embedding) |
| `POST /agents/ingest/neo4j` | Celery | Chunks → Neo4j (LLM extraction) |
| `POST /agents/pipeline` | Celery | Full chain: extract → ingest → graph |
| `PUT /agents/config` | Sync | Update LLM config |
| `GET /agents/graph/stats` | Sync | Neo4j node/relationship counts |
| `GET /tasks/{id}` | Sync | Task status + progress |
| `GET /tasks` | Sync | List active tasks |
| `DELETE /tasks/{id}` | Sync | Cancel task |

## Possible Future Enhancements

| Enhancement | What it adds | Priority |
|-------------|-------------|----------|
| **PostgreSQL checkpointer** | LangGraph conversation persistence across Redis flushes | Nice-to-have |
| **MLflow integration** | Track RAG quality metrics, compare models, log costs | Nice-to-have |
| **Celery Beat** | Scheduled ingestion (auto-detect new videos) | After testing |
| **Prometheus + Grafana** | Query latency, cache hit rate, token costs | After production |
| **RAG evaluation (RAGAS)** | Golden test sets, faithfulness scores | Important |
| **Community detection** | Neo4j graph algorithms for topic clusters | Nice-to-have |
| **Multi-modal (Phase 5)** | Video frame extraction + visual search | Future |

## Neo4j Knowledge Graph — Assessment & Roadmap

### Current State (8/10)

| Aspect | Status | Notes |
|--------|:------:|-------|
| Entity extraction (LLMGraphTransformer) | **Done** | Unconstrained, format-guided, full transcripts |
| Entity resolution (rapidfuzz) | **Done** | 75% threshold, normalizes + merges duplicates |
| Celery background processing | **Done** | Runs overnight, real-time Neo4j updates |
| Video/Channel metadata graph | **Done** | MERGE-based, idempotent |
| Skip already-processed videos | **Done** | Checks Neo4j before LLM call |
| Money amount false merge fix | **Done** | Excludes numeric entities from fuzzy match |
| Improved Neo4j retriever | **Done** | Multi-pattern Cypher traversal |

### Future Improvements

| Improvement | Impact | Priority |
|-------------|--------|:--------:|
| Community detection (Louvain/Label Propagation) | Topic clusters, content grouping | Medium |
| Temporal analysis (filter by upload_date) | Track opinion evolution over time | Medium |
| Auto-schema discovery per channel | Better entity types per domain | Low |
| Graph embeddings (Node2Vec/GraphSAGE) | Graph-aware semantic search | Advanced |
| Entity normalization via LLM aliases | Reduce duplicates at extraction time | Low |

---

*Last updated: 2026-04-12*
