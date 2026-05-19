# YCS Migration — Legacy Walkthrough + SOTA Architecture (May 2026)

**Date:** 2026-05-19
**Scope:** Migration reference for the YouTube Content Search (YCS) subsystem from `zdeprecated/` to a new SOTA implementation.
**Constraint anchors:**
- `[[project_local_vs_rotator_architecture]]` — no inference inside COELHO Cloud K8s; hosted rotators only.
- `[[project_planner_architecture_2026_05_17]]` — committed 10-node LITA-hybrid graph design.
- `[[feedback_youtube_transcript]]` — only Playwright CDP worked for transcripts as of late 2025 (partially obsolete — see §3.2).

---

## 1. TL;DR

The 2026 SOTA pattern for what YCS does has a name: **"Adaptive Graph-RAG with Corrective Self-Refinement and Orchestrator-Worker Deep Research."** It composes six published patterns wired into LangGraph 1.0 durable execution:

1. **Adaptive-RAG** (arXiv 2403.14403) — small-LM classifier routes by complexity
2. **HippoRAG 2** (ICLR'26 GraphRAG-Bench winner) — PPR over dual passage+phrase graph
3. **CRAG** — grade → decompose → web-fallback → re-retrieve
4. **Anthropic orchestrator-worker** — deep-mode fan-out with isolated worker contexts
5. **STORM/Co-STORM** — multi-perspective Q-gen for long-form synthesis
6. **RAGAS + FaithJudge** — programmatic hallucination gate

**Top-5 ROI deltas vs legacy** (priority migration order):

1. **HippoRAG 2 over direct-Cypher 1-hop** — +7-10 F1 on GraphRAG-Bench, 13× cheaper indexing.
2. **jina-reranker-v3 + BGE-M3 hybrid + Anthropic contextual prefixes** — multilingual, 32K context, 49% recall lift.
3. **CRAG corrective loop + RAGAS/FaithJudge critic** — replaces ad-hoc `rewrite_query` + binary LLM check.
4. **TF-IDF/DistilBERT router** — replaces LLM classifier, 28% token savings per query at higher accuracy.
5. **FalkorDB → Neo4j swap** — sub-140ms p99, native multi-tenancy. Only ROI-positive if #1 ships.

---

## 2. Legacy YCS — Step-by-Step

### 2.1 Ingestion path (Celery chain)

1. **Discover** — `POST /youtube/content/{videos|channel|playlist}` enqueues `extract_*` → `helpers.YtDlpExtractor` runs yt-dlp subprocess with filters (duration, date, views, live, channel, regex).
2. **Transcribe** — `PlaywrightTranscriptService` (CDP browser pool, sem=5) two-tier retry: direct caption-URL → DOM fallback. Skip videos already in ES.
3. **Index to ES** — bulk-write `coelhonexus-youtube-metadata` + `coelhonexus-youtube-transcriptions` (composite id `{video_id}_{lang}`).
4. **Embed → Qdrant** — `ingest_to_qdrant` scrolls ES, chunks (RecursiveCharacterTextSplitter 2000/200), embeds dense (NVIDIA NIM `llama-nemotron-embed-1b-v2`, 2048d) + sparse (FastEmbed BM25), upserts to `youtube-transcripts` (RRF hybrid). Deterministic MD5 ids.
5. **Build KG → Neo4j** — `ingest_to_neo4j` sends full transcripts (not chunks) to `LLMGraphTransformer` via Groq→NVIDIA NIM fallback, batch=3, 2s pacing. Entity resolution: normalize → exact-match → rapidfuzz 75% → APOC `mergeNodes`.
6. **Invalidate cache** — Redis RAG cache cleared. `full_channel_pipeline` chains 1→5→6 via Celery `chain()`.

### 2.2 Query path (Adaptive RAG, LangGraph)

1. `POST /youtube/agents/search[/stream]` — loads conversation history (Postgres by `thread_id`), checks Redis answer cache.
2. **AdaptiveRAGGraph** routes by complexity: **fast** → `direct_answer`; **standard** → embedded RAG subgraph; **deep** → `plan_research` decomposes 3-8 sub-questions → `Send()` fan-out → N parallel `run_subagent` → `synthesize` → `critic` with confidence_score.
3. **Standard RAG subgraph** — `retrieve` (SmartRetriever: Qdrant hybrid + Neo4j graph in parallel via `asyncio.gather`, ES fallback) → `rerank` (FlashRank, top-10) → `grade_documents` (LLM binary, parallel) → `generate` (NVIDIA NIM 8-model fallback) → `check_hallucination` → `format_citations`. Self-correct on empty-retrieval or ungrounded: `rewrite_query` loop (max=3).
4. **Persist** — answer cached in Redis (stateless threads only); Q&A saved to Postgres for next turn's `contextualize`.

### 2.3 Legacy architecture (ASCII)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          CLIENT (HTTP / SSE)                             │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                ┌──────────────────┼──────────────────┐
                ▼                  ▼                  ▼
       /content/* (CRUD)    /agents/search       /agents/ingest/*
                │                  │                  │
                ▼                  │                  ▼
   CELERY CHAIN:                   │       CELERY (qdrant/neo4j queues)
   extract_* → ingest_to_qdrant    │
   → ingest_to_neo4j → invalidate  │
                │                  │
                ▼                  ▼
   helpers.py:                AdaptiveRAGGraph (LangGraph):
   ┌──────────────────┐       contextualize → classify
   │YtDlpExtractor    │       ├─fast─→ direct_answer ───→ END
   │PlaywrightCDPool  │       ├─standard─→ run_standard ─→ END
   │  sem=5, 2-tier   │       └─deep─→ plan_research
   │  retry           │             → Send fan-out
   └────────┬─────────┘             → run_subagent×N
            │                       → synthesize → critic → END
            ▼
   Elasticsearch ─┬─→ ingestion.py ──→ Qdrant (dense+BM25,RRF)
                  ├─→ graph_builder ──→ Neo4j (LLMGraphTransformer)
                  └─→ retriever.py ──→ SmartRetriever
                                       (Qdrant ∥ Neo4j, ES fallback)
                                       → FlashRank → grade → generate

   Stores: Elasticsearch · Qdrant · Neo4j · Redis · Postgres
   LLM:    NVIDIA NIM 8-model chain · Groq (KG extract)
```

---

## 3. SOTA Stage-by-Stage (May 2026)

| # | Stage | Legacy | SOTA 2026 | Justification |
|---|---|---|---|---|
| 1 | Discovery / metadata | yt-dlp subprocess | **yt-dlp ≥2026.03.17 + bgutil-ytdlp-pot-provider 1.3.1 + Deno + cookies** | Still #1; per-video PO tokens now required, Deno JS runtime mandatory, OAuth login killed. |
| 2 | Transcript extraction | Playwright CDP pool | **Primary: yt-dlp `--write-auto-subs --skip-download`** (reuses PO-token session). **Fallback: Patchright** (undetected Playwright fork). | "Only Playwright" claim partially obsolete; yt-dlp+PO covers most videos in one session. Patchright drops headless-detection from 100% → ~67%. `youtube-transcript-api` and `pytubefix` confirmed broken in 2026. |
| 3 | Chunking | LangChain Recursive 2000/200 chars | **Chonkie `RecursiveChunker`** 400-512 tokens / 75 overlap, sentence-bounded, fillers stripped, `{video_id, start_ts, speaker}` metadata | Token-aware vs char-aware; Chonkie 33× faster than LangChain. Recursive beats semantic chunkers in 2026 Vecta study (69% vs 54%). |
| 4 | Context enrichment | none | **Anthropic contextual retrieval** (cheap-LM prepends 50-100 token context per chunk; prompt-cache parent transcript) | 49% retrieval-failure reduction; transcripts (pronouns, topic-jumps) benefit more than codebases. ~$12 / 1k docs with caching. |
| 5 | Dense embeddings | NIM `llama-nemotron-embed-1b-v2` (2048d, 8K) | **Qwen3-Embedding-4B** (MRL 32→2560d, 32K ctx, Apache 2.0, hosted SiliconFlow/DeepInfra) | MTEB Multilingual #1 (Qwen3-8B = 70.58, 4B ≈ 69), 4× context, MRL allows cheap dim cuts, free-tier rotator-friendly. |
| 6 | Sparse retrieval | FastEmbed BM25 | **BGE-M3 sparse head** (same model emits dense + sparse + ColBERT) | One forward pass yields hybrid; +10 nDCG@10 over dense on long-doc retrieval (MLDR). Skip SPLADE-v3 (heavier ops). |
| 7 | Vector DB | Qdrant hybrid | **Keep Qdrant v1.13+** with Hybrid Cloud K8s Operator; absorb ES role (dense+sparse+filter+RRF in one Query API call) | Only true peer is Milvus 2.5 at 50M+ vectors. Matches "DBs on-cluster, no inference on-cluster" rule. |
| 8 | Hybrid fusion | RRF inside Qdrant | **RRF k=60 inside Qdrant**; **DBSF when merging across stores** | RRF is parameter-free and score-scale-immune; DBSF when scales diverge across backends. |
| 9 | Multi-vector / ColBERT | none | **Skip** | Storage cost 10-100× single-vector; reranker captures the precision delta. Adopt only if going visual/ColPali. |
| 10 | Query transform | none | **Confidence-gated HyDE only** (no default multi-query) | Multi-query gains absorbed by rerank; HyDE adds 25-50% nDCG@10 on hard queries only. |
| 11 | Reranker | FlashRank (~50ms, EN) | **jina-reranker-v3** 0.6B (32K ctx, 100+ langs, ~200ms K=20, 61.94 nDCG@10 BEIR) | Beats Qwen3-Reranker-4B at 6× smaller; multilingual + transcript-length. Keep FlashRank as `KD_FAST_RERANK` fallback. |
| 12 | Adaptive top-k | static k=10 | **CAR — Cluster-based Adaptive Retrieval** (arXiv 2511.14769) | 60% token reduction, 22% latency drop, 10% fewer hallucinations vs fixed top-k. |
| 13 | KG extraction | `LLMGraphTransformer` full-transcript, open-domain | **LlamaIndex `DynamicLLMPathExtractor`** chunked 1200/100 + Pydantic-schema-guided ({Person, Channel, Concept, Product, Event}) + GLiNER-Relex/Triplex fallback | Full-transcript is worst input length; schema-guided cuts hallucinated triples 40% (OG-RAG). GLiNER-Relex matches existing GLiNER preference. |
| 14 | Entity resolution | rapidfuzz 75% + APOC merge | **rapidfuzz/Splink blocking → LLM-CER in-context clustering (SIGMOD 2025) → MERGE** | LLM-CER 98% F1 on OpenSanctions Pairs vs rapidfuzz baseline. |
| 15 | Graph DB | Neo4j + APOC | **FalkorDB GraphRAG-SDK 1.0** (Cypher-compatible, multi-tenant) | #1 on GraphRAG-Bench (Novel + Medical); sub-140ms p99 vs Neo4j's multi-second tail; 3× faster point, 2.9× 2-hop. Memgraph 3.8 = strong 2nd. Kuzu archived by Apple Oct-2025. |
| 16 | Graph retrieval | LLM `ExtractedEntities` → Cypher 1-hop | **HippoRAG 2** (Personalized PageRank on dual passage+phrase graph + LLM triple filter) | Highest overall on GraphRAG-Bench: +F1 7-10pt over MS-GraphRAG; indexing 13× cheaper (9M vs 115M tokens). |
| 17 | Full-text store | Elasticsearch | **Collapse into Qdrant sparse** (BGE-M3 sparse head replaces ES BM25) | ES needed only for ESRE/ELSER-grade sparse — neither required here. Removes one backend. |
| 18 | Response cache | Redis SHA-256 exact-match | **Redis L1 (SHA-256) + RedisVL semantic L2** (or vCache for verified-threshold) | vCache 12.5× higher hit rate vs static-threshold GPTCache; RedisVL is lower-friction on existing Redis. |
| 19 | Conversation memory | Postgres per-thread | **Postgres (LangGraph checkpointer)** + **Mem0 sidecar** keyed by user_id | Mem0 ~1.7k tokens/conversation vs Zep ~600k; 78% extraction / 94% retrieval. Surfaced as `user_context` state field. |
| 20 | Orchestrator | LangGraph (legacy) | **LangGraph 1.0** (durable execution + Postgres checkpointer) | Upgrade in place. DSPy is a complement, not replacement. |
| 21 | Query router | LLM classifier | **TF-IDF + SVM** (or DistilBERT, ~15ms) with fast/standard/deep + GraphRAG-LOCAL/GLOBAL lanes | RAGRouter-Bench 0.928 macro-F1, beats embedding routers by 3.1 F1 with 28% token savings. |
| 22 | Self-correction | `rewrite_query` on empty retrieval | **Full CRAG loop**: grade → if poor, decompose + web-search fallback → re-retrieve | Production 2026 default for closed-API stacks. Cap retries=3. |
| 23 | Deep-mode planning | `plan_research` → `Send()` fan-out → synthesize → critic | **Anthropic orchestrator-worker**: lead planner → 3-5 parallel worker subgraphs (isolated contexts) → synthesizer; **STORM/Co-STORM multi-perspective Q-gen** for long-form reports | +90.2% over single-agent Opus on Anthropic's eval. STORM hits 99% factuality and 70% human preference vs search. |
| 24 | Critic / hallucination gate | binary LLM grounded? | **RAGAS faithfulness + groundedness** with **FaithJudge** few-shot, threshold-gated retry | FaithJudge has highest human agreement for hallucination detection in 2026. |
| 25 | Streaming | SSE | **LangGraph `astream_events(version='v2')` over SSE for browser**; **MCP servers on Streamable HTTP** (SSE deprecated in MCP 2026) | Single `/mcp` endpoint, no session affinity, K8s-friendly. |
| 26 | Generator models | NIM 8-model fallback | **GLM-5 → DeepSeek V4-Pro (NIM) → Kimi K2.6 → Gemini 3.1 Pro → Qwen3-235B (Cerebras) → Llama-3.3-70B-spec (Groq) → Nemotron-3-Super** | All hosted, all free-tier or sub-$2/M; replaces deprecated Nemotron-49/70b + Llama-3.1-8B. |
| 27 | KG-extraction models | Groq (llama-3.3-70b / qwen3-32b / llama-3.1-8b) → NIM | **GLM-5** (MIT, native JSON-schema) → **Qwen3-235B on Cerebras** → **DeepSeek V4-Flash on NIM** → Llama-3.3-70B-spec on Groq | GLM-5 tuned for structured extraction. Cerebras free 1M tok/day for bulk. |
| 28 | Cheap/fast (grade/rewrite/classify) | NIM small models | **Gemini 3.1 Flash-Lite** ($0.25/$1.50, 382 tok/s) → **Llama-3.3-70B-spec on Groq** (1,665 tok/s) → **Qwen3-32B on Cerebras** | Sub-second TTFT, 94% intent-classification, free tier on AI Studio + Groq + Cerebras. |
| 29 | Provider gateway | direct httpx | **LiteLLM** as unified gateway under **ParetoBandit** arm selector | 100+ providers, native streaming + failover; bandit picks above LiteLLM. |
| 30 | Long-context vs RAG | RAG only | **Hybrid baseline**: RAG retrieves, long-context (1M Gemini 3.1 / Claude Opus 4.7) reasons over retrieved set | RAG wins >400K, multi-doc, citations required, cost-sensitive. Long-ctx wins single ≤400K doc + multi-step reasoning. |

---

## 4. SOTA Architecture — ASCII Graph

```
                      ┌──────────────────────────────────────────┐
                      │            CLIENT (HTTP / SSE)           │
                      └────────────────────┬─────────────────────┘
                                           ▼
              ┌──────────────────────────────────────────────────────────┐
              │             FastAPI · LangGraph 1.0 entry                │
              │            (durable exec, PG checkpointer)               │
              └─────────┬─────────────────────────────────┬──────────────┘
                        │ query                           │ ingest
                        ▼                                 ▼
              ╔═════════════════════════╗      ┌──────────────────────┐
              ║  TF-IDF/DistilBERT      ║      │ Celery / orchestrator│
              ║  Router (15ms)          ║      │  ingestion chain     │
              ║  fast / standard /      ║      └─────────┬────────────┘
              ║  deep + LOCAL/GLOBAL    ║                │
              ╚════╤═════╤═════╤════════╝                ▼
            fast   │   standard  │  deep        ┌──────────────────┐
              ┌────▼────┐ ┌──────▼─────┐ ┌──────▼─────┐ Discovery   │
              │ direct  │ │ Adaptive   │ │ Anthropic  │  yt-dlp +   │
              │ answer  │ │ Hybrid RAG │ │ Orchestr.- │  bgutil-PoT │
              │ (LLM)   │ │ (subgraph) │ │ Worker     │  + Deno     │
              └────┬────┘ └──────┬─────┘ └──────┬─────┘ + cookies   │
                   │             │              │       └────┬─────┘
                   │             │      ┌───────▼──────┐     │
                   │             │      │ planner ──┐  │     ▼
                   │             │      │ Send×N    │  │  Transcripts:
                   │             │      │ workers   │  │  yt-dlp subs
                   │             │      │ (isolated │  │  → Patchright
                   │             │      │  ctx)     │  │  fallback
                   │             │      │   │       │  │     │
                   │             │      │   ▼       │  │     │
                   │             │      │ synthesize│  │     ▼
                   │             │      │   │       │  │  Chonkie
                   │             │      │   ▼       │  │  RecursiveChunker
                   │             │      │ STORM     │  │  400-512 tok
                   │             │      │ multi-    │  │  + strip fillers
                   │             │      │ perspect. │  │     │
                   │             │      └─────┬─────┘  │     ▼
                   │             │            │        │  Anthropic
                   │             │            │        │  contextual
                   │             │            │        │  prefix (cheap LM)
                   │             │            │        │     │
                   │   ╔═════════▼═════════╗  │        │     ▼
                   │   ║ CRAG self-correct ║  │        │  Embed: Qwen3-
                   │   ║  retrieve         ║  │        │  Embedding-4B
                   │   ║  → grade (RAGAS)  ║  │        │  + BGE-M3 sparse
                   │   ║  → if poor:       ║  │        │     │
                   │   ║    decompose      ║  │        │     ▼
                   │   ║    + web fallback ║  │        │  Qdrant upsert
                   │   ║  → re-retrieve    ║  │        │  (dense+sparse,
                   │   ║  (max 3)          ║  │        │   RRF k=60)
                   │   ╚═════════╤═════════╝  │        │     │
                   │             │            │        │     ▼
                   │             ▼            │        │  Schema-guided
                   │   ┌───────────────────┐  │        │  KG extract:
                   │   │ Hybrid Retrieval  │  │        │  LlamaIndex
                   │   │   parallel:       │  │        │  DynamicLLM-
                   │   │ ┌──────────────┐  │  │        │  PathExtractor
                   │   │ │Qdrant hybrid │  │  │        │  (chunked 1200)
                   │   │ │dense+sparse  │  │  │        │     │
                   │   │ │RRF k=60      │  │  │        │     ▼
                   │   │ └──────┬───────┘  │  │        │  Entity resolve:
                   │   │ ┌──────▼───────┐  │  │        │  rapidfuzz block
                   │   │ │HippoRAG 2    │  │  │        │  → LLM-CER
                   │   │ │PPR on dual   │  │  │        │  → MERGE
                   │   │ │passage+phrase│  │  │        │     │
                   │   │ │graph         │  │  │        │     ▼
                   │   │ │(FalkorDB)    │  │  │        │  FalkorDB
                   │   │ └──────┬───────┘  │  │        │  (multi-tenant)
                   │   │ DBSF merge        │  │        └─────┬────────┘
                   │   └──────┬────────────┘  │              │
                   │          ▼               │              │
                   │   ┌─────────────┐        │              │
                   │   │CAR adaptive │        │              │
                   │   │top-k        │        │              │
                   │   └──────┬──────┘        │              │
                   │          ▼               │              │
                   │   ┌─────────────┐        │              │
                   │   │jina-reranker│        │              │
                   │   │-v3 (0.6B)   │        │              │
                   │   │100+ langs,  │        │              │
                   │   │32K ctx      │        │              │
                   │   └──────┬──────┘        │              │
                   │          ▼               │              │
                   │   ┌─────────────┐        │              │
                   │   │HyDE? (conf- │        │              │
                   │   │gated only)  │        │              │
                   │   └──────┬──────┘        │              │
                   │          ▼               │              │
                   │   ┌─────────────────┐    │              │
                   │   │ generate w/     │    │              │
                   │   │ citations       │◀───┼──────────────┤
                   │   │ (LiteLLM under  │    │              │
                   │   │  ParetoBandit)  │    │              │
                   │   └─────────┬───────┘    │              │
                   │             ▼            │              │
                   │   ┌─────────────────┐    │              │
                   │   │ Critic:         │    │              │
                   │   │ RAGAS faithful  │    │              │
                   │   │ + FaithJudge    │    │              │
                   │   │ (threshold gate)│    │              │
                   │   └─────────┬───────┘    │              │
                   │             │            │              │
                   └──────┬──────┴────────────┘              │
                          ▼                                  │
              ┌───────────────────────────┐                  │
              │ stream: astream_events v2 │                  │
              │ MCP servers: Streamable   │                  │
              │  HTTP (SSE deprecated)    │                  │
              └────────────┬──────────────┘                  │
                           ▼                                 │
                        CLIENT                               │
                                                             │
  ╔══════════════════════════════════════════════════════════════╗
  ║                       STATE LAYER                            ║
  ╠══════════════════════════════════════════════════════════════╣
  ║  Qdrant v1.13 ── dense+sparse+filter+RRF (absorbs ES)        ║
  ║  FalkorDB    ── GraphRAG SDK 1.0, multi-tenant, vec+text     ║
  ║  Postgres 18 ── LangGraph checkpointer + chat turns          ║
  ║  Mem0        ── cross-session user memory (sidecar)          ║
  ║  Redis L1    ── SHA-256 exact cache                          ║
  ║  Redis L2    ── RedisVL/vCache semantic cache                ║
  ╚══════════════════════════════════════════════════════════════╝

  ╔══════════════════════════════════════════════════════════════╗
  ║         INFERENCE LAYER  (ALL HOSTED, NO IN-CLUSTER)         ║
  ╠══════════════════════════════════════════════════════════════╣
  ║  RAG-gen pool (ParetoBandit / LiteLLM):                      ║
  ║   GLM-5 → DeepSeek V4-Pro → Kimi K2.6 → Gemini 3.1 Pro       ║
  ║   → Qwen3-235B (Cerebras) → Llama-3.3-70B-spec (Groq)        ║
  ║   → Nemotron-3-Super (NIM)                                   ║
  ║  KG-extract pool:                                            ║
  ║   GLM-5 → Qwen3-235B → DeepSeek V4-Flash → Llama-3.3-70B     ║
  ║  Cheap/fast pool (grade/rewrite/classify):                   ║
  ║   Gemini 3.1 Flash-Lite → Llama-3.3-70B-spec → Qwen3-32B     ║
  ║  Reranker: jina-reranker-v3 (hosted) / FlashRank fallback    ║
  ║  Embeddings: Qwen3-Embedding-4B + BGE-M3 sparse (hosted)     ║
  ╚══════════════════════════════════════════════════════════════╝
```

---

## 5. Backend consolidation (5 stores → 4)

| Role | Legacy | SOTA | Change |
|---|---|---|---|
| Dense + sparse + full-text | Qdrant + Elasticsearch | Qdrant v1.13 only | **Drop ES** — Qdrant absorbs BM25 via BGE-M3 sparse head. |
| Knowledge graph | Neo4j | FalkorDB GraphRAG-SDK 1.0 | **Swap** — same Cypher, faster, multi-tenant native. |
| Conversation memory | Postgres | Postgres 18 + pgvector + LangGraph checkpointer | Keep, upgrade. |
| Cross-session memory | none | Mem0 (sidecar) | **Add**, optional. |
| Response cache | Redis SHA-256 | Redis L1 (SHA-256) + L2 (RedisVL/vCache semantic) | Layered. |

---

## 6. Open Decisions

Things to confirm before implementation starts:

1. **FalkorDB vs Neo4j ROI** — migration cost is non-trivial. Only proceed if HippoRAG 2 is going in (it implies graph traversal load that benefits from FalkorDB). Otherwise keep Neo4j 2026.04.
2. **Mem0 sidecar** — adds a service. Defer until cross-session memory is a product requirement, not a "nice to have."
3. **Patchright vs yt-dlp `--write-auto-subs`** — start with yt-dlp-only path; only add Patchright tier if videos fail caption-URL fetch in measurable %.
4. **Long-context fallback model** — Gemini 3.1 Pro (1M) vs Claude Opus 4.7 (1M, ~76% MRCR v2). Pick on cost/latency for your specific deep-mode reports.
5. **STORM layer** — only add for genuine report-grade outputs; skip for chat-grade deep-mode.
6. **CAR adaptive top-k** — paper is recent (arXiv 2511.14769); validate locally before adopting.

---

## 7. Sources (by cluster)

### 7.1 Extraction + transcript
- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp 2026 Guide - Pickuma](https://pickuma.com/posts/yt-dlp-cli-video-downloader-2026/)
- [yt-dlp PO Token Wiki](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
- [youtube-transcript-api PyPI](https://pypi.org/project/youtube-transcript-api/) — broken on PO-token-gated videos
- [Patchright (undetected Playwright fork)](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
- [Patchright deep dive - ZenRows](https://www.zenrows.com/blog/patchright)
- [Playwright Stealth 2026 - Scrapewise](https://scrapewise.ai/blogs/playwright-stealth-2026)
- [Best YouTube Transcript API 2026 - Supadata](https://supadata.ai/blog/best-youtube-transcript-api)
- [Bypassing 2026 YouTube Great Wall - DEV](https://dev.to/ali_ibrahim/bypassing-the-2026-youtube-great-wall-a-guide-to-yt-dlp-v2rayng-and-sabr-blocks-1dk8)

### 7.2 Embeddings + sparse + reranker
- [MTEB April 2026 leaderboard](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-april-2026/)
- [Qwen3-Embedding-8B HuggingFace](https://huggingface.co/Qwen/Qwen3-Embedding-8B)
- [Qwen3-Embedding-4B (MRL dims)](https://huggingface.co/Qwen/Qwen3-Embedding-4B)
- [Qwen3 Embedding paper - arXiv 2506.05176](https://arxiv.org/pdf/2506.05176)
- [BGE-M3 HuggingFace](https://huggingface.co/BAAI/bge-m3)
- [BGE-M3 paper - arXiv 2402.03216](https://arxiv.org/html/2402.03216v3)
- [jina-reranker-v3 blog](https://jina.ai/news/jina-reranker-v3-0-6b-listwise-reranker-for-sota-multilingual-retrieval/)
- [jina-reranker-v3 paper - arXiv 2509.25085](https://arxiv.org/html/2509.25085v2)
- [mxbai-rerank-v2 blog](https://www.mixedbread.com/blog/mxbai-rerank-v2)
- [Best Embedding Models for RAG 2026 - Milvus](https://milvus.io/blog/choose-embedding-model-rag-2026.md)
- [Best Rerankers 2026 - BSWEN](https://docs.bswen.com/blog/2026-02-25-best-reranker-models/)

### 7.3 KG extraction + GraphRAG
- [GraphRAG-Bench (ICLR'26)](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark)
- [When to use Graphs in RAG - arXiv 2506.05690](https://arxiv.org/html/2506.05690v3)
- [HippoRAG GitHub (OSU-NLP-Group)](https://github.com/OSU-NLP-Group/HippoRAG)
- [HippoRAG 2 - MarkTechPost](https://www.marktechpost.com/2025/03/03/hipporag-2-advancing-long-term-memory-and-contextual-retrieval-in-large-language-models/)
- [LightRAG GitHub](https://github.com/HKUDS/LightRAG)
- [LlamaIndex PropertyGraphIndex customization](https://www.llamaindex.ai/blog/customizing-property-graph-index-in-llamaindex)
- [LlamaIndex schema-guided extraction](https://developers.llamaindex.ai/python/examples/property_graph/property_graph_advanced/)
- [GLiNER-Relex - arXiv 2605.10108](https://arxiv.org/abs/2605.10108v1)
- [Triplex (SciPhi)](https://huggingface.co/SciPhi/Triplex)
- [LLM-CER In-context clustering ER - arXiv 2506.02509](https://arxiv.org/abs/2506.02509)
- [OpenSanctions Pairs LLM ER benchmark - arXiv 2603.11051](https://arxiv.org/pdf/2603.11051)
- [FalkorDB GraphRAG SDK 1.0](https://www.openpr.com/news/4494136/falkordb-ships-graphrag-sdk-1-0-ranks-1-on-graphrag-bench)
- [FalkorDB GraphRAG SDK GitHub](https://github.com/FalkorDB/GraphRAG-SDK)
- [FalkorDB vs Neo4j benchmark](https://www.falkordb.com/blog/graph-database-performance-benchmarks-falkordb-vs-neo4j/)
- [Neo4j Alternatives 2026 - ArcadeDB](https://arcadedb.com/blog/neo4j-alternatives-in-2026-a-fair-look-at-the-open-source-options/)
- [Graph RAG in 2026 — Practitioner's Guide](https://medium.com/graph-praxis/graph-rag-in-2026-a-practitioners-guide-to-what-actually-works-dca4962e7517)

### 7.4 Chunking + retrieval
- [Jina late chunking](https://jina.ai/news/late-chunking-in-long-context-embedding-models/)
- [Late chunking paper - arXiv 2409.04701](https://arxiv.org/html/2409.04701v3)
- [Chonkie GitHub](https://github.com/chonkie-inc/chonkie)
- [Chonkie benchmarks](https://github.com/chonkie-inc/chonkie/blob/main/BENCHMARKS.md)
- [RAG Chunking Strategies 2026 Benchmark](https://blog.premai.io/rag-chunking-strategies-the-2026-benchmark-guide/)
- [Anthropic contextual retrieval](https://www.anthropic.com/news/contextual-retrieval)
- [ConTEB: Reconstructing Context - arXiv 2504.19754](https://arxiv.org/pdf/2504.19754)
- [Context is Gold - arXiv 2505.24782](https://arxiv.org/pdf/2505.24782)
- [Fusion Functions for Hybrid Retrieval - arXiv 2210.11934](https://arxiv.org/pdf/2210.11934)
- [RRF vs DBSF Qdrant](https://haikel-fazzani.deno.dev/blog/rrf-vs-dbsf-qdrant)
- [Qdrant Hybrid Queries docs](https://qdrant.tech/documentation/search/hybrid-queries/)
- [CAR Cluster-based Adaptive Retrieval - arXiv 2511.14769](https://arxiv.org/abs/2511.14769)
- [Adaptive-k - arXiv 2506.08479](https://arxiv.org/pdf/2506.08479)
- [YouTube transcripts to KG - Neo4j blog](https://neo4j.com/blog/developer/youtube-transcripts-knowledge-graphs-rag/)

### 7.5 Agentic RAG architecture
- [LangGraph 1.0 GA](https://changelog.langchain.com/announcements/langgraph-1-0-is-now-generally-available)
- [LangChain/LangGraph 1.0 blog](https://blog.langchain.com/langchain-langgraph-1dot0/)
- [Agentic RAG 2026 Guide - MarsDevs](https://www.marsdevs.com/guides/agentic-rag-2026-guide)
- [Agentic RAG survey - arXiv 2501.09136](https://arxiv.org/abs/2501.09136)
- [RAGRouter-Bench - arXiv 2604.03455](https://arxiv.org/abs/2604.03455)
- [Co-STORM - arXiv 2408.15232](https://arxiv.org/abs/2408.15232)
- [LATS - arXiv 2310.04406](https://arxiv.org/abs/2310.04406)
- [Anthropic multi-agent research system](https://www.zenml.io/llmops-database/building-a-multi-agent-research-system-for-complex-information-tasks)
- [Self-correcting retrieval](https://letsdatascience.com/blog/agentic-rag-self-correcting-retrieval)
- [CRAG - Kore.ai](https://www.kore.ai/blog/corrective-rag-crag)
- [LangGraph streaming docs](https://docs.langchain.com/oss/python/langchain/streaming)
- [MCP 2026 - Streamable HTTP](https://zylos.ai/research/2026-03-26-agent-interoperability-protocols-mcp-a2a-acp-convergence)
- [State of AI Agent Memory 2026 - Mem0](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [LLM evaluation frameworks compared - Atlan](https://atlan.com/know/llm-evaluation-frameworks-compared/)

### 7.6 Storage + cache
- [Best Vector Databases in 2026 - MarkTechPost](https://www.marktechpost.com/2026/05/10/best-vector-databases-in-2026-pricing-scale-limits-and-architecture-tradeoffs-across-nine-leading-systems/)
- [Vector Database Benchmarks 2026 - CallSphere](https://callsphere.ai/blog/vector-database-benchmarks-2026-pgvector-qdrant-weaviate-milvus-lancedb)
- [VectorDBBench Leaderboard - Zilliz](https://zilliz.com/vdbbench-leaderboard?dataset=vectorSearch)
- [Hybrid Search Guide April 2026 - Supermemory](https://supermemory.ai/blog/hybrid-search-guide/)
- [Qdrant Hybrid Cloud K8s Operator](https://qdrant.tech/documentation/hybrid-cloud/)
- [Turbopuffer pricing](https://turbopuffer.com/pricing)
- [Vespa unified platform](https://vespa.ai/why-vespa/)
- [Memgraph 3.8 Atomic GraphRAG](https://memgraph.com/blog/memgraph-3-8-release-atomic-graphrag-vector-single-store-parallel-runtime)
- [vCache: Verified Semantic Prompt Caching - arXiv 2502.03771](https://arxiv.org/abs/2502.03771)
- [Semantic Caching 2026 - Spheron](https://www.spheron.network/blog/semantic-cache-llm-inference-gpu-cloud/)
- [RedisVL Semantic Cache](https://docs.redisvl.com/en/latest/user_guide/03_llmcache.html)
- [Elasticsearch vs hybrid reality 2026](https://pureinsights.com/blog/2026/from-vector-hype-to-hybrid-reality-is-elasticsearch-still-the-right-bet/)
- [pgvector RAG production 2026](https://danubedata.ro/blog/pgvector-rag-managed-postgres-2026)

### 7.7 LLM providers + serving
- [Artificial Analysis leaderboard](https://artificialanalysis.ai/leaderboards/models)
- [DeepSeek V4-Pro providers](https://artificialanalysis.ai/models/deepseek-v4-pro/providers)
- [Kimi K2.6](https://artificialanalysis.ai/models/kimi-k2-6)
- [Gemini 3.1 Flash-Lite blog](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-flash-lite/)
- [Best AI Models May 2026](https://www.buildfastwithai.com/blogs/best-ai-models-may-2026-leaderboard)
- [DeepSeek V4-Pro on NIM](https://freellm.net/models/nvidia-nim/deepseek-ai-deepseek-v4-pro/)
- [NVIDIA NIM compute guide](https://yangmao.ai/en/compute/nvidia-nim/)
- [Nemotron 3 Super blog](https://developer.nvidia.com/blog/introducing-nemotron-3-super-an-open-hybrid-mamba-transformer-moe-for-agentic-reasoning/)
- [Groq free tier limits 2026](https://tokenmix.ai/blog/groq-free-tier-limits-2026)
- [Cerebras CS-3 vs Groq LPU](https://www.cerebras.ai/blog/cerebras-cs-3-vs-groq-lpu)
- [Best Open-Source LLMs for KG construction - SiliconFlow](https://www.siliconflow.com/articles/en/best-open-source-LLM-for-Knowledge-Graph-Construction)
- [GLM model family](https://kili-technology.com/blog/data-story-glm-model-family)
- [GLM-5 on OpenRouter](https://openrouter.ai/z-ai/glm-5)
- [Long-context vs RAG 2026](https://www.digitalapplied.com/blog/long-context-retrieval-needle-in-haystack-2026)
- [LiteLLM routing docs](https://docs.litellm.ai/docs/routing-load-balancing)

---

## 8. Provenance

This document is the synthesis of 7 parallel deep-research agents (each running 19-39 web/doc searches anchored to 2026 sources) plus 4 parallel code-exploration agents that mapped the legacy `zdeprecated/` YCS implementation. Session date: 2026-05-19.
