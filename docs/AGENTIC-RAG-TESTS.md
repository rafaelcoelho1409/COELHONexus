# Agentic RAG — Endpoint Test Guide

> Complete test suite for the YouTube Content Search Agentic RAG system.
> Architecture: FastAPI (API) + Celery (background tasks) + NVIDIA NIM (LLM + embeddings)
> Data: **402 transcriptions** from the **Wealthy Expat** channel (`UC49PyeVkVY6godW0pF6H8Pg`).

**Base URL (Skaffold):** `http://localhost:23020`
**Flower Dashboard:** `https://celery-flower.YOUR_TAILNET_DOMAIN.ts.net`
**Neo4j Browser:** `http://neo4j.YOUR_TAILNET_DOMAIN.ts.net:7474`
**Qdrant Dashboard:** `https://qdrant.YOUR_TAILNET_DOMAIN.ts.net/dashboard`

---

## Table of Contents

1. [Health Check](#1-health-check)
2. [Content Crawlers (Celery)](#2-content-crawlers)
3. [Qdrant Ingest (Celery + NVIDIA NIM Embeddings)](#3-qdrant-ingest)
4. [Neo4j Graph Ingest (Celery + LLM Entity Extraction)](#4-neo4j-graph-ingest)
5. [Agentic RAG Search (Sync)](#5-agentic-rag-search)
6. [SSE Streaming Search](#6-sse-streaming-search)
7. [Task Management](#7-task-management)
8. [Full Pipeline](#8-full-pipeline)
9. [LLM Configuration](#9-llm-configuration)
10. [Cache Behavior](#10-cache-behavior)
11. [Edge Cases](#11-edge-cases)

---

## 1. Health Check

```bash
curl -s $BASE/health
```

**Expected:** `{"status": "healthy", "service": "COELHO Nexus"}`

**Startup logs should show:**
```
Qdrant connected: 1 collections
Neo4j connected: bolt://neo4j.YOUR_TAILNET_DOMAIN.ts.net:7687
Embedding models will lazy-load on first use.
Neo4j LangChain graph initialized.
LLM loaded: z-ai/glm5 + 13 fallbacks (NVIDIA NIM)
FastAPI startup complete.
```

**Pods running:**
```bash
kubectl get pods -n coelhonexus-dev
# coelhonexus-fastapi-xxx         2/2   Running
# coelhonexus-celery-worker-xxx   2/2   Running
# coelhonexus-web-xxx             1/1   Running
```

---

## 2. Content Crawlers

All crawler endpoints run as **Celery background tasks**. They return a task_id immediately.

### 2.1 Search (sync — no Celery)

```bash
curl -s -X POST $BASE/api/v1/youtube/content/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "citizenship by investment", "max_results": 3}'
```

**Expected:** Immediate response with search results (no task_id, runs synchronously).

### 2.2 Extract Videos (Celery)

```bash
# Submit
curl -s -X POST $BASE/api/v1/youtube/content/videos \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["VIDEO_ID_1", "VIDEO_ID_2"], "include_transcription": true}'

# Response: {"task_id": "...", "status": "queued", "endpoint": "/api/v1/tasks/..."}

# Poll status
curl -s $BASE/api/v1/tasks/{task_id}
```

**Expected:** Task completes with `{"total_videos": 2, "metadata": {"indexed": 2}, "transcriptions": {"indexed": 2}}`

### 2.3 Extract Channel (Celery)

```bash
curl -s -X POST $BASE/api/v1/youtube/content/channel \
  -H 'Content-Type: application/json' \
  -d '{"channel_id": "TechWorldwithNana", "max_results": 3, "include_transcription": true}'
```

**Expected:** Task completes with channel metadata + videos extracted + transcriptions indexed.

### 2.4 Extract Playlist (Celery)

```bash
curl -s -X POST $BASE/api/v1/youtube/content/playlist \
  -H 'Content-Type: application/json' \
  -d '{"playlist_id": "PLUc76aQXizyOqOKE0Cspsv8Bpkujv75N3", "max_results": 3, "include_transcription": true}'
```

**Expected:** Task completes with playlist videos extracted.

---

## 3. Qdrant Ingest

Ingests transcripts from ES → chunks → NVIDIA NIM embedding API (2048d) → Qdrant.
Runs as Celery task. **Zero CPU usage** (API-based embeddings).

### 3.1 Ingest Specific Videos

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/qdrant \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["7a_KPrvhJi8", "YjeMkwVMwOM"]}'
```

### 3.2 Ingest ALL Transcripts

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/qdrant \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": null}'
```

**Expected:** `{"total_transcripts": 402, "total_chunks": ~2900, "points_upserted": ~2900, "embedding": "nvidia-nim-api"}`

### 3.3 Verify in Qdrant Dashboard

Open `https://qdrant.YOUR_TAILNET_DOMAIN.ts.net/dashboard`:
- Collection: `youtube-transcripts`
- Vectors: `dense` (2048d, cosine) + `sparse`
- Points: ~3266

### 3.4 Test Cosine Similarity Directly

```bash
# Requires NVIDIA_API_KEY and QDRANT_API_KEY
# Embed a query, then search Qdrant
curl -sk -H "api-key: $QDRANT_API_KEY" -X POST \
  "https://qdrant.YOUR_TAILNET_DOMAIN.ts.net/collections/youtube-transcripts/points/query" \
  -H "Content-Type: application/json" \
  -d '{"query": [VECTOR], "using": "dense", "limit": 3, "with_payload": true}'
```

**Relevance benchmark (tested 2026-04-11):**
- Relevant query ("citizenship by investment Caribbean"): score ~0.58
- Semantic match ("pay less taxes moving abroad"): score ~0.49
- Irrelevant ("quantum physics black hole"): score ~0.12

---

## 4. Neo4j Graph Ingest

Extracts entities and relationships from **full transcripts** via LLM → stores in Neo4j.
Runs as Celery task. 1 LLM call per transcript (not chunked).
~402 LLM calls total, ~40-60 minutes.

### 4.1 Ingest All Transcripts

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/neo4j \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": null, "batch_size": 3}'
```

### 4.2 Ingest Specific Videos

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/neo4j \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["7a_KPrvhJi8", "VFEijm3Z57U"], "batch_size": 3}'
```

### 4.3 Check Graph Stats

```bash
curl -s $BASE/api/v1/youtube/agents/graph/stats
```

**Expected:** Node counts by label (Person, Country, Organization, etc.) + relationship counts by type.

### 4.4 Verify in Neo4j Browser

Open `http://neo4j.YOUR_TAILNET_DOMAIN.ts.net:7474`:

```cypher
-- Visual graph (best overview)
MATCH (n:__Entity__)-[r]-(m)
WHERE NOT m:Document
RETURN n, r, m
LIMIT 200

-- Node types discovered
MATCH (n) WHERE NOT n:Document AND NOT n:__Entity__
UNWIND labels(n) AS label
RETURN label, count(*) AS count ORDER BY count DESC

-- Relationship types
MATCH ()-[r]->()
RETURN type(r) AS type, count(*) AS count ORDER BY count DESC

-- Most connected entities
MATCH (n:__Entity__)-[r]-()
RETURN n.id, labels(n), count(r) AS connections
ORDER BY connections DESC LIMIT 20
```

### 4.5 Features

- **No schema constraints** — LLM captures all entity/relationship types
- **Format-guided instructions** — TitleCase nodes, UPPER_SNAKE_CASE relationships
- **Entity resolution** — rapidfuzz merges duplicates (75% threshold) after extraction
- **Works for any channel** — no domain-specific config needed

---

## 5. Agentic RAG Search

Full pipeline: Retrieve (Qdrant hybrid + Neo4j graph + ES fallback) → Grade → Generate → Hallucination Check → Citations.

### 5.1 Basic Search

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What are the cheapest countries for citizenship by investment?"}'
```

**Expected response:**
```json
{
  "answer": "Based on the transcripts, Dominica and St. Lucia...",
  "citations": [{"video_id": "...", "title": "...", "source": "qdrant_hybrid"}],
  "grounded": true,
  "retrieval_sources": ["qdrant_hybrid", "neo4j_graph"],
  "retry_count": 0,
  "search_query": "What are the cheapest..."
}
```

**What to verify:**
- `retrieval_sources` includes `qdrant_hybrid` (vector search working)
- `grounded: true` (hallucination check passed)
- `citations` have video titles and URLs
- Answer references specific content from transcripts

### 5.2 Semantic Query (Different Vocabulary)

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "How can wealthy people legally reduce their tax burden by moving abroad?"}'
```

**Expected:** Finds tax optimization content even though transcripts use different words.

### 5.3 Multi-Source Query (Neo4j + Qdrant)

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What does Wealthy Expat say about Dubai for crypto investors?"}'
```

**Expected:** `retrieval_sources: ["qdrant_hybrid", "neo4j_graph"]` — both contributing.

### 5.4 Query Triggering Rewrite

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What does Wealthy Expat say about quantum computing?", "max_retries": 1}'
```

**Expected:** `retry_count: 1`, `grounded: false`, empty/no answer.

### 5.5 Comparative Query

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "Compare citizenship by investment programs of Dominica, Saint Kitts, and Grenada"}'
```

**Expected:** Answer synthesizes from multiple videos, 3+ citations.

---

## 6. SSE Streaming Search

Same pipeline, but streams node-by-node progress via Server-Sent Events.

```bash
curl -N -X POST $BASE/api/v1/youtube/agents/search/stream \
  -H 'Content-Type: application/json' \
  -d '{"question": "What are the tax benefits of living in Dubai?"}'
```

**Expected SSE events:**
```
data: {"node": "retrieve", "document_count": 5}
data: {"node": "grade_documents", "document_count": 3}
data: {"node": "generate", "generation": "Based on..."}
data: {"node": "check_hallucination"}
data: {"node": "format_citations"}
data: {"node": "end", "status": "complete"}
```

---

## 7. Task Management

### 7.1 Check Task Status

```bash
curl -s $BASE/api/v1/tasks/{task_id}
```

**States:** `PENDING` → `STARTED` → `PROGRESS` → `SUCCESS` or `FAILURE`

### 7.2 List Active Tasks

```bash
curl -s $BASE/api/v1/tasks
```

### 7.3 Cancel a Task

```bash
curl -s -X DELETE $BASE/api/v1/tasks/{task_id}
```

### 7.4 Monitor via Flower

Open `https://celery-flower.YOUR_TAILNET_DOMAIN.ts.net`:
- Active/completed/failed tasks
- Worker status and resource usage
- Queue depth

---

## 8. Full Pipeline

One call triggers: extract channel → ingest Qdrant → ingest Neo4j → clear cache.

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/pipeline \
  -H 'Content-Type: application/json' \
  -d '{
    "channel_id": "TechWorldwithNana",
    "max_results": 5,
    "include_transcription": true,
    "include_qdrant": true,
    "include_graph": false
  }'
```

**Expected:** Returns task_id. Each step runs sequentially in Celery.

---

## 9. LLM Configuration

### 9.1 Check Current LLM

```bash
kubectl logs -n coelhonexus-dev deploy/coelhonexus-fastapi -c coelhonexus-fastapi-container | grep "LLM loaded"
```

**Default:** `z-ai/glm5 + 13 fallbacks (NVIDIA NIM)`

### 9.2 Update LLM Config

```bash
curl -s -X PUT $BASE/api/v1/youtube/agents/config \
  -H 'Content-Type: application/json' \
  -d '{"provider": "NVIDIA", "model": "meta/llama-3.3-70b-instruct", "temperature": 0.0}'
```

**Note:** Requires FastAPI restart to take effect.

---

## 10. Cache Behavior

### 10.1 Verify Cache Hit

```bash
# First call: full pipeline (~20-30s)
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "Dubai golden visa"}'

# Second call: instant (<1s, from cache)
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "Dubai golden visa"}'
# Check: "_from_cache": true
```

### 10.2 Cache Invalidation

Cache is automatically cleared when you run `POST /agents/ingest`.

---

## 11. Edge Cases

### 11.1 Empty Question

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": ""}'
```

### 11.2 Non-Existent Video IDs

```bash
curl -s -X POST $BASE/api/v1/youtube/content/videos \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["NONEXISTENT123"]}'
```

### 11.3 Concurrent Requests

```bash
for q in "Dubai tax" "Portugal visa" "Caribbean passport" "Serbia citizenship" "crypto nomad"; do
  curl -s -X POST $BASE/api/v1/youtube/agents/search \
    -H 'Content-Type: application/json' \
    -d "{\"question\": \"$q\"}" &
done
wait
```

---

## Architecture Summary

| Component | Technology | Role |
|-----------|-----------|------|
| **FastAPI** | Python 3.13 | API server (sync search, task enqueue) |
| **Celery** | Redis broker | Background tasks (crawling, ingestion, graph) |
| **Flower** | mher/flower | Celery monitoring dashboard |
| **NVIDIA NIM** | 14 LLM models + embedding API | Generation, grading, entity extraction, embeddings |
| **Qdrant** | Hybrid (dense 2048d + BM25 sparse) | Semantic + keyword search |
| **Neo4j** | Community Edition + APOC | Knowledge graph (entities + relationships) |
| **Elasticsearch** | v8 | Raw data storage + full-text fallback |
| **Redis Stack** | v7.4 | Cache, LangGraph checkpoints, Celery broker |
| **FlashRank** | Cross-encoder | Reranking retrieved documents |

---

*Last updated: 2026-04-12*
