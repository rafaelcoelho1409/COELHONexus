# Agentic RAG — Endpoint Test Guide

> Complete test suite for the YouTube Content Search Agentic RAG system.
> Uses **359 transcriptions** from the **Wealthy Expat** channel (`UC49PyeVkVY6godW0pF6H8Pg`).
> Channel content: citizenship by investment, tax optimization, expat relocation, golden visas.

**Base URL (Skaffold):** `http://localhost:23020`
**Base URL (ArgoCD):** `http://localhost:23000`

Replace `$BASE` with the appropriate URL in the commands below.

---

## Table of Contents

1. [Health Check](#1-health-check)
2. [PUT /config — LLM Configuration](#2-put-config)
3. [POST /ingest — Qdrant Vector Ingestion](#3-post-ingest)
4. [POST /search — Agentic RAG Search](#4-post-search)
5. [POST /search/stream — SSE Streaming Search](#5-post-searchstream)
6. [POST /ingest/graph — Neo4j Entity Extraction](#6-post-ingestgraph)
7. [GET /graph/stats — Knowledge Graph Statistics](#7-get-graphstats)
8. [End-to-End Flow](#8-end-to-end-flow)
9. [Edge Cases and Failure Modes](#9-edge-cases)

---

## 1. Health Check

Verify the app started and all connections are live.

```bash
curl -s $BASE/health | python3 -m json.tool
```

**Expected:**
```json
{"status": "healthy", "service": "COELHO Nexus"}
```

**What to check in startup logs:**
```
Qdrant connected: X collections
Neo4j connected: bolt://neo4j.YOUR_TAILNET_DOMAIN.ts.net:7687
Embedding models loaded (bge-base dense + BM25 sparse)
Neo4j LangChain graph initialized.
Redis checkpointer initialized.
```

If any line is missing, the corresponding Phase won't work.

---

## 2. PUT /config

**What it does:** Updates the LLM provider/model used by ALL agent operations (grading, generation, rewriting, hallucination check, entity extraction).

### Test 2.1 — Set NVIDIA NIM (default)

```bash
curl -s -X PUT $BASE/api/v1/youtube/agents/config \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "NVIDIA",
    "model": "meta/llama-3.3-70b-instruct",
    "temperature": 0.0,
    "base_url": "https://integrate.api.nvidia.com/v1",
    "api_key": "YOUR_NVIDIA_API_KEY"
  }' | python3 -m json.tool
```

**Expected:**
```json
{
    "status": "saved",
    "config": {
        "provider": "NVIDIA",
        "model": "meta/llama-3.3-70b-instruct",
        "temperature": 0.0,
        "base_url": "https://integrate.api.nvidia.com/v1"
    }
}
```

**How it works internally:**
- Saves to Redis key `coelhonexus:youtube:agents:config` as JSON
- `api_key` is excluded from the response (security)
- On next startup, `app.py` loads this config to instantiate the LLM
- Does NOT hot-reload — restart needed for changes to take effect

### Test 2.2 — Verify config persisted in Redis

```bash
# From the pod or via redis-cli
redis-cli -h redis-tcp.YOUR_TAILNET_DOMAIN.ts.net -a $REDIS_PASSWORD \
  JSON.GET coelhonexus:youtube:agents:config
```

---

## 3. POST /ingest

**What it does:** Reads transcriptions from Elasticsearch, chunks them (2000 chars, 200 overlap), generates dense embeddings (bge-base-en-v1.5, 768d) + sparse embeddings (BM25), upserts to Qdrant hybrid collection `youtube-transcripts`.

**Cost:** FREE — all embeddings computed locally on CPU.
**Time:** ~5-15 min for 359 transcripts (depends on CPU).

### Test 3.1 — Ingest specific videos (small test)

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "video_ids": ["7a_KPrvhJi8", "YjeMkwVMwOM", "WM126J84rcw"],
    "chunk_size": 2000,
    "chunk_overlap": 200
  }' | python3 -m json.tool
```

**Expected:**
```json
{
    "total_transcripts": 3,
    "total_chunks": 30,
    "points_upserted": 30,
    "collection_created": true,
    "embedding_model": "bge-base",
    "collection": "youtube-transcripts"
}
```

**What to verify:**
- `total_transcripts`: should match number of video_ids with transcripts in ES
- `total_chunks`: each transcript (~8000 chars) / chunk_size (2000) ≈ 4-5 chunks per video
- `points_upserted` = `total_chunks` (every chunk becomes a Qdrant point)
- `collection_created: true` on first run, `false` on subsequent runs
- Check Qdrant dashboard: `https://qdrant.YOUR_TAILNET_DOMAIN.ts.net/dashboard` → collection `youtube-transcripts`

### Test 3.2 — Ingest ALL transcripts

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": null}' | python3 -m json.tool
```

**Expected:** `total_transcripts: 359`, `total_chunks: ~1500-2000`, `points_upserted: ~1500-2000`.

### Test 3.3 — Re-ingest (idempotency test)

Run the same ingest again:

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["7a_KPrvhJi8"]}' | python3 -m json.tool
```

**Expected:** Same stats. Point count in Qdrant should NOT increase — deterministic IDs (MD5 of video_id + chunk_index) mean same content overwrites existing points.

### Test 3.4 — Custom chunk parameters

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "video_ids": ["VFEijm3Z57U"],
    "chunk_size": 1000,
    "chunk_overlap": 100
  }' | python3 -m json.tool
```

**Expected:** More chunks per video (smaller chunk_size = more pieces).
Useful for testing whether smaller chunks improve search precision.

---

## 4. POST /search

**What it does:** Full agentic RAG pipeline:
1. **Retrieve** — SmartRetriever: Qdrant hybrid (dense + BM25) + Neo4j graph (if available), parallel, deduplicated, reranked with FlashRank
2. **Grade** — LLM grades each document for relevance (parallel via asyncio.gather)
3. **Generate** — LLM produces answer with citations from relevant documents
4. **Check Hallucination** — LLM verifies answer is grounded in documents
5. **Format Citations** — Extracts structured source links
6. If grading/hallucination fails → **Rewrite Query** → retry from step 1

**Cost:** LLM tokens (grading × N docs + generation + hallucination check + possible rewrites).

### Test 4.1 — Basic factual query

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "What countries does Wealthy Expat recommend for citizenship by investment?"
  }' | python3 -m json.tool
```

**Expected response structure:**
```json
{
    "answer": "Based on the transcripts, Wealthy Expat discusses several countries for citizenship by investment including Saint Kitts and Nevis, Dominica, Grenada, Vanuatu... [Video: Best Alternatives to Caribbean Citizenship by Investment]...",
    "citations": [
        {
            "video_id": "7a_KPrvhJi8",
            "title": "Best Alternatives to Caribbean Citizenship by Investment",
            "channel": "Wealthy Expat",
            "url": "https://www.youtube.com/watch?v=7a_KPrvhJi8",
            "source": "qdrant_hybrid"
        }
    ],
    "grounded": true,
    "retrieval_sources": ["qdrant_hybrid"],
    "retry_count": 0,
    "search_query": "What countries does Wealthy Expat recommend for citizenship by investment?"
}
```

**What to verify:**
- `answer`: should reference specific countries mentioned in transcripts, not generic knowledge
- `citations`: deduplicated by video_id, each with title + URL
- `grounded: true`: hallucination check passed
- `retrieval_sources`: shows which retriever(s) contributed
- `retry_count: 0`: no rewrites needed (good query + relevant content)

### Test 4.2 — Semantic query (different vocabulary)

Tests Qdrant dense vectors — query uses different words than the transcripts.

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "How can wealthy people legally reduce their tax burden by moving abroad?"
  }' | python3 -m json.tool
```

**Why this matters:** The transcripts say "tax optimization" and "offshore strategies", not "reduce tax burden". ES keyword search might miss this. Qdrant dense vectors should match the semantic meaning.

**Expected:** Answer about tax strategies from transcripts, `retrieval_sources: ["qdrant_hybrid"]`.

### Test 4.3 — Keyword-specific query (BM25 sparse shines)

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Saint Kitts and Nevis citizenship $150,000 investment"
  }' | python3 -m json.tool
```

**Why this matters:** Exact terms like "Saint Kitts and Nevis" and "$150,000" are best matched by BM25 sparse vectors. The dense model might confuse it with other Caribbean nations.

### Test 4.4 — Query triggering rewrite

Content that doesn't exist — forces the agent to rewrite and retry.

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "What does Wealthy Expat say about quantum computing?",
    "max_retries": 2
  }' | python3 -m json.tool
```

**Expected:**
```json
{
    "answer": "No answer generated.",
    "citations": [],
    "grounded": false,
    "retry_count": 2,
    "search_query": "quantum computing technology videos wealthy expat channel"
}
```

**What to verify:**
- `retry_count: 2`: agent rewrote the query twice before giving up
- `search_query`: different from original question (was rewritten by LLM)
- `answer`: should clearly state no relevant information found

### Test 4.5 — Cache hit test

Run the same query twice:

```bash
# First call — full pipeline (~5-15 seconds)
time curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is the best country for a digital nomad visa?"}' > /dev/null

# Second call — should be instant (<100ms)
time curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is the best country for a digital nomad visa?"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'from_cache: {d.get(\"_from_cache\", False)}')"
```

**Expected:** Second call returns `_from_cache: True` in <100ms.

### Test 4.6 — Thread persistence (conversation memory)

Same thread_id across multiple queries — tests checkpointer.

```bash
# First question
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "What are the pros of Portugal golden visa?",
    "thread_id": "conversation-001"
  }' | python3 -m json.tool

# Second question on same thread
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "And what about Greece compared to that?",
    "thread_id": "conversation-001"
  }' | python3 -m json.tool
```

**What to verify:** The second query might benefit from checkpointed state (the graph knows the previous context for this thread).

### Test 4.7 — Hallucination detection test

Ask for specific numbers/facts — harder for the LLM to fabricate.

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "What exact investment amounts are required for Saint Kitts citizenship?"
  }' | python3 -m json.tool
```

**What to verify:**
- `grounded: true`: all amounts should be from the transcripts
- If the LLM fabricates a number not in the sources, `grounded` should be `false` and a retry should trigger

### Test 4.8 — Multi-video comparative query

Tests whether the system pulls from multiple videos.

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Compare the citizenship by investment programs of Belarus, Saint Kitts, and Greece. What are the costs and benefits of each?"
  }' | python3 -m json.tool
```

**Expected:** Answer synthesizes information from multiple videos. `citations` should list 3+ different videos.

---

## 5. POST /search/stream

**What it does:** Same pipeline as `/search`, but streams node-by-node updates via Server-Sent Events (SSE). The client sees real-time progress.

### Test 5.1 — Basic streaming

```bash
curl -N -X POST $BASE/api/v1/youtube/agents/search/stream \
  -H 'Content-Type: application/json' \
  -d '{"question": "What are the tax benefits of living in Dubai?"}'
```

**Expected output (SSE events, one per node):**

```
data: {"node": "retrieve", "documents": [...], "document_count": 10}

data: {"node": "grade_documents", "documents": [...], "document_count": 4}

data: {"node": "generate", "generation": "Based on the transcripts..."}

data: {"node": "check_hallucination"}

data: {"node": "format_citations"}

data: {"node": "end", "status": "complete"}
```

**What to verify:**
- Events arrive incrementally (not all at once)
- `retrieve` shows raw document count before grading
- `grade_documents` shows filtered count (fewer docs)
- `generate` contains the full answer text
- Sequence matches the graph: retrieve → grade → generate → check → format → end

### Test 5.2 — Streaming with rewrite (observe retry loop)

```bash
curl -N -X POST $BASE/api/v1/youtube/agents/search/stream \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "What does Wealthy Expat think about Mars colonization?",
    "max_retries": 1
  }'
```

**Expected:** You'll see the rewrite cycle in real-time:
```
data: {"node": "retrieve", "document_count": 5}
data: {"node": "grade_documents", "document_count": 0}
data: {"node": "rewrite_query", "search_query": "space exploration mars wealthy expat", "retry_count": 1}
data: {"node": "retrieve", "document_count": 3}
data: {"node": "grade_documents", "document_count": 0}
data: {"node": "end", "status": "complete"}
```

### Test 5.3 — Parse SSE events programmatically (Python)

```python
import requests
import json

url = "http://localhost:23020/api/v1/youtube/agents/search/stream"
payload = {"question": "Best European countries for tax residency?"}

with requests.post(url, json=payload, stream=True) as r:
    for line in r.iter_lines():
        if line:
            text = line.decode("utf-8")
            if text.startswith("data: "):
                event = json.loads(text[6:])
                print(f"[{event['node']}]", end=" ")
                if "document_count" in event:
                    print(f"docs={event['document_count']}")
                elif "generation" in event:
                    print(f"answer={event['generation'][:100]}...")
                elif "retry_count" in event:
                    print(f"retry={event['retry_count']} query={event['search_query']}")
                else:
                    print()
```

---

## 6. POST /ingest/graph

**What it does:** Two-step process:
1. Creates Video + Channel nodes from metadata (FREE — direct Cypher, no LLM)
2. Extracts Topic, Person, Technology, Concept entities from transcript chunks via LLMGraphTransformer (COSTS LLM TOKENS)

**Cost:** ~1 LLM call per chunk. For 5 videos ≈ ~25 chunks ≈ ~25 LLM calls.

### Test 6.1 — Small test (5 videos)

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/graph \
  -H 'Content-Type: application/json' \
  -d '{
    "video_ids": [
      "7a_KPrvhJi8",
      "VFEijm3Z57U",
      "Lx5GkAS5d7Q",
      "mTdWcsiPnDM",
      "6AKEQPPvJ5s"
    ],
    "batch_size": 5,
    "chunk_size": 2000,
    "chunk_overlap": 200
  }' | python3 -m json.tool
```

**Expected:**
```json
{
    "videos_processed": 5,
    "chunks_processed": 25,
    "documents_processed": 25,
    "nodes_created": 80,
    "relationships_created": 120
}
```

**What to verify:**
- `videos_processed`: matches input count
- `nodes_created`: entities extracted (Topics, Persons, Technologies, etc.)
- `relationships_created`: connections between entities (DISCUSSES, MENTIONS, etc.)
- Check Neo4j Browser: `http://neo4j.YOUR_TAILNET_DOMAIN.ts.net:7474`

### Test 6.2 — Verify graph in Neo4j Browser

Open `http://neo4j.YOUR_TAILNET_DOMAIN.ts.net:7474` and run these Cypher queries:

```cypher
-- See all nodes
MATCH (n) RETURN n LIMIT 100

-- See Video → Channel relationships
MATCH (v:Video)-[:BELONGS_TO]->(c:Channel) RETURN v.title, c.name LIMIT 20

-- See what topics are discussed
MATCH (v:Video)-[:DISCUSSES]->(t:Topic) RETURN v.title, t.id LIMIT 30

-- See people mentioned
MATCH (v:Video)-[:MENTIONS]->(p:Person) RETURN v.title, p.id LIMIT 20

-- Find topics shared between multiple videos
MATCH (v1:Video)-[:DISCUSSES]->(t:Topic)<-[:DISCUSSES]-(v2:Video)
WHERE v1 <> v2
RETURN t.id AS topic, collect(DISTINCT v1.title) AS videos
LIMIT 10

-- Count nodes by type
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(*) AS count
ORDER BY count DESC
```

### Test 6.3 — Batch size impact

Smaller batch_size = fewer concurrent LLM calls = slower but less API pressure:

```bash
# Conservative (2 concurrent LLM calls per batch)
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/graph \
  -H 'Content-Type: application/json' \
  -d '{
    "video_ids": ["WM126J84rcw", "YjeMkwVMwOM"],
    "batch_size": 2
  }' | python3 -m json.tool
```

### Test 6.4 — Re-ingest same videos (idempotency)

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/graph \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["7a_KPrvhJi8"], "batch_size": 5}' | python3 -m json.tool
```

**What to verify:** Run the stats endpoint before and after — node count should NOT double. `MERGE` statements in `build_video_metadata_graph` are idempotent. However, LLMGraphTransformer + `add_graph_documents` may create duplicate entity nodes if the LLM extracts slightly different names. Check for near-duplicates in Neo4j.

---

## 7. GET /graph/stats

**What it does:** Returns node and relationship counts from Neo4j, grouped by label/type.

```bash
curl -s $BASE/api/v1/youtube/agents/graph/stats | python3 -m json.tool
```

**Expected:**
```json
{
    "total_nodes": 150,
    "total_relationships": 200,
    "nodes_by_label": {
        "__Entity__": 120,
        "Topic": 45,
        "Video": 5,
        "Person": 25,
        "Channel": 1,
        "Technology": 15,
        "Concept": 30,
        "Document": 25
    },
    "relationships_by_type": {
        "DISCUSSES": 80,
        "MENTIONS": 40,
        "BELONGS_TO": 5,
        "RELATED_TO": 30,
        "FEATURES": 20,
        "USES": 25
    }
}
```

**What to verify:**
- `__Entity__` count = sum of all entity types (every entity gets this base label)
- `Document` count = number of chunks processed (source tracking)
- `Video` count = number of videos ingested via `/ingest/graph`
- `Channel` count = 1 (all videos from same channel)
- `BELONGS_TO` count = `Video` count (each video belongs to one channel)

---

## 8. End-to-End Flow

Complete test sequence from zero to multi-source search:

```bash
BASE="http://localhost:23020"

# 1. Health check
echo "=== Health Check ==="
curl -s $BASE/health

# 2. Ingest 5 videos to Qdrant (free, ~1 min)
echo -e "\n\n=== Qdrant Ingestion ==="
curl -s -X POST $BASE/api/v1/youtube/agents/ingest \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["7a_KPrvhJi8","VFEijm3Z57U","Lx5GkAS5d7Q","mTdWcsiPnDM","6AKEQPPvJ5s"]}'

# 3. Search (Qdrant hybrid only — no Neo4j data yet)
echo -e "\n\n=== Search (Qdrant only) ==="
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "Which Caribbean islands offer citizenship by investment?"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Sources: {d[\"retrieval_sources\"]}\nGrounded: {d[\"grounded\"]}\nRetries: {d[\"retry_count\"]}\nCitations: {len(d[\"citations\"])}\nAnswer: {d[\"answer\"][:200]}...')"

# 4. Ingest to Neo4j (costs LLM tokens, ~2 min for 5 videos)
echo -e "\n\n=== Neo4j Graph Ingestion ==="
curl -s -X POST $BASE/api/v1/youtube/agents/ingest/graph \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["7a_KPrvhJi8","VFEijm3Z57U","Lx5GkAS5d7Q","mTdWcsiPnDM","6AKEQPPvJ5s"], "batch_size": 5}'

# 5. Check graph stats
echo -e "\n\n=== Graph Stats ==="
curl -s $BASE/api/v1/youtube/agents/graph/stats | python3 -m json.tool

# 6. Search (Qdrant + Neo4j in parallel)
echo -e "\n\n=== Search (Multi-source) ==="
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What countries and people are mentioned in relation to golden visa programs?"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Sources: {d[\"retrieval_sources\"]}\nGrounded: {d[\"grounded\"]}\nCitations: {len(d[\"citations\"])}\nAnswer: {d[\"answer\"][:300]}...')"

# 7. Cache hit test
echo -e "\n\n=== Cache Test ==="
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What countries and people are mentioned in relation to golden visa programs?"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'From cache: {d.get(\"_from_cache\", False)}')"
```

---

## 9. Edge Cases and Failure Modes

### Test 9.1 — Empty question

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": ""}' | python3 -m json.tool
```

**Expected:** Validation error (422) or empty result.

### Test 9.2 — Very long question

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d "{\"question\": \"$(python3 -c "print('What about taxes? ' * 500)")\"}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('answer','')[:200])"
```

**Expected:** Should handle gracefully — the retriever truncates or the LLM manages the long input.

### Test 9.3 — Non-existent video IDs in ingest

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/ingest \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["NONEXISTENT123"]}' | python3 -m json.tool
```

**Expected:** `total_transcripts: 0, total_chunks: 0, points_upserted: 0`.

### Test 9.4 — Max retries exhausted

```bash
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Explain the recipe for chocolate cake",
    "max_retries": 1
  }' | python3 -m json.tool
```

**Expected:** `retry_count: 1`, no relevant answer. Agent gave up after 1 rewrite attempt.

### Test 9.5 — Ingest invalidates cache

```bash
# First: search and cache a result
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "Dubai tax benefits"}' > /dev/null

# Verify it's cached
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "Dubai tax benefits"}' \
  | python3 -c "import sys,json; print(f'cached: {json.load(sys.stdin).get(\"_from_cache\", False)}')"
# Should print: cached: True

# Now ingest (this clears all cache)
curl -s -X POST $BASE/api/v1/youtube/agents/ingest \
  -H 'Content-Type: application/json' \
  -d '{"video_ids": ["7a_KPrvhJi8"]}' > /dev/null

# Verify cache was cleared
curl -s -X POST $BASE/api/v1/youtube/agents/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "Dubai tax benefits"}' \
  | python3 -c "import sys,json; print(f'cached: {json.load(sys.stdin).get(\"_from_cache\", False)}')"
# Should print: cached: False (full pipeline runs again)
```

### Test 9.6 — Concurrent requests

```bash
# Fire 5 searches in parallel
for q in \
  "Best country for crypto investors" \
  "Portugal golden visa requirements" \
  "Dubai tax free living" \
  "Serbia citizenship by merit" \
  "Caribbean passport investment cost"; do
  curl -s -X POST $BASE/api/v1/youtube/agents/search \
    -H 'Content-Type: application/json' \
    -d "{\"question\": \"$q\"}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{d[\"search_query\"][:40]:40s} | grounded={d[\"grounded\"]} | citations={len(d[\"citations\"])}')" &
done
wait
echo "All done"
```

**Expected:** All 5 return valid results. Tests concurrent LLM calls, Qdrant queries, and Redis checkpointing under load.

---

*Last updated: 2026-04-10*
